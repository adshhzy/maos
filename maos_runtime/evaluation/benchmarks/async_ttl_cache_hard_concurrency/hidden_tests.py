import asyncio
import threading
import time

import pytest

from maos_cache import AsyncTTLCache, CacheInfo, async_ttl_cache, ttl_cache


def test_falsy_values_are_cached_by_direct_sync_api():
    cache = AsyncTTLCache(maxsize=16, ttl=60)

    for key, value in {
        "none": None,
        "false": False,
        "zero": 0,
        "empty": "",
    }.items():
        calls = {"count": 0}

        def factory():
            calls["count"] += 1
            return value

        assert cache.get_or_set(key, factory) is value
        assert cache.get_or_set(key, lambda: "should-not-run") is value
        assert calls["count"] == 1


def test_decorator_key_normalizes_defaults_kwargs_and_positional_equivalence():
    calls = {"count": 0}

    @ttl_cache(maxsize=32, ttl=60)
    def compute(value, scale=1, *, offset=0, label="x"):
        calls["count"] += 1
        return f"{label}:{value * scale + offset}:{calls['count']}"

    expected = "x:3:1"
    assert compute(3) == expected
    assert compute(value=3) == expected
    assert compute(3, scale=1) == expected
    assert compute(3, offset=0, scale=1) == expected
    assert compute(value=3, label="x", offset=0, scale=1) == expected
    assert calls["count"] == 1
    assert compute.cache_info().hits >= 4


def test_sync_get_or_set_is_single_flight_across_threads():
    cache = AsyncTTLCache(maxsize=8, ttl=60)
    calls = {"count": 0}
    started = threading.Event()
    release = threading.Event()

    def factory():
        calls["count"] += 1
        started.set()
        assert release.wait(timeout=2)
        return "shared"

    results = []
    errors = []

    def worker():
        try:
            results.append(cache.get_or_set("same", factory))
        except BaseException as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=1)
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert results == ["shared"] * 12
    assert calls["count"] == 1
    info = cache.cache_info()
    assert isinstance(info, CacheInfo)
    assert info.misses == 1
    assert info.hits >= 11
    assert info.inflight == 0


def test_async_waiter_cancellation_does_not_cancel_leader_or_shared_result():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        calls = {"count": 0}
        entered = asyncio.Event()

        async def factory():
            calls["count"] += 1
            entered.set()
            await asyncio.sleep(0.08)
            return "value"

        leader = asyncio.create_task(cache.aget_or_set("k", factory))
        await asyncio.wait_for(entered.wait(), timeout=1)
        waiter = asyncio.create_task(cache.aget_or_set("k", factory))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert await leader == "value"
        assert calls["count"] == 1
        assert cache.cache_info().inflight == 0
        assert await cache.aget_or_set("k", factory) == "value"
        assert calls["count"] == 1

    asyncio.run(scenario())


def test_clear_while_async_factory_inflight_prevents_stale_recache():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        entered = asyncio.Event()

        async def slow_factory():
            entered.set()
            await asyncio.sleep(0.05)
            return "old"

        task = asyncio.create_task(cache.aget_or_set("k", slow_factory))
        await asyncio.wait_for(entered.wait(), timeout=1)
        before = cache.clear()
        assert isinstance(before, CacheInfo)
        assert await task == "old"

        calls = {"count": 0}

        def replacement():
            calls["count"] += 1
            return "new"

        assert cache.get_or_set("k", replacement) == "new"
        assert calls["count"] == 1

    asyncio.run(scenario())


def test_invalidate_while_async_factory_inflight_prevents_target_stale_recache():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        cache.get_or_set("other", lambda: "keep")
        entered = asyncio.Event()

        async def slow_factory():
            entered.set()
            await asyncio.sleep(0.05)
            return "old"

        task = asyncio.create_task(cache.aget_or_set("target", slow_factory))
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert cache.invalidate("target") is True
        assert await task == "old"
        assert cache.get_or_set("other", lambda: "lost") == "keep"

        calls = {"count": 0}
        assert cache.get_or_set("target", lambda: calls.__setitem__("count", calls["count"] + 1) or "new") == "new"
        assert calls["count"] == 1

    asyncio.run(scenario())


def test_different_async_keys_run_independently_not_under_global_await_lock():
    async def scenario():
        cache = AsyncTTLCache(maxsize=32, ttl=60)

        async def factory(value):
            await asyncio.sleep(0.08)
            return value

        started = time.perf_counter()
        results = await asyncio.gather(
            *(cache.aget_or_set(f"k-{idx}", lambda idx=idx: factory(idx)) for idx in range(6))
        )
        elapsed = time.perf_counter() - started
        assert sorted(results) == list(range(6))
        assert elapsed < 0.28

    asyncio.run(scenario())


def test_concurrent_async_exception_executes_once_broadcasts_and_retries_cleanly():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        calls = {"count": 0}

        async def failing_factory():
            calls["count"] += 1
            await asyncio.sleep(0.05)
            raise RuntimeError("boom")

        tasks = [asyncio.create_task(cache.aget_or_set("bad", failing_factory)) for _ in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(item, RuntimeError) for item in results)
        assert calls["count"] == 1
        assert cache.cache_info().inflight == 0

        async def success_factory():
            calls["count"] += 1
            return "ok"

        assert await cache.aget_or_set("bad", success_factory) == "ok"
        assert calls["count"] == 2

    asyncio.run(scenario())


def test_async_decorator_key_normalizes_signature_equivalent_calls():
    async def scenario():
        calls = {"count": 0}

        @async_ttl_cache(maxsize=32, ttl=60)
        async def compute(value, scale=1, *, offset=0, label="x"):
            calls["count"] += 1
            await asyncio.sleep(0)
            return f"{label}:{value * scale + offset}:{calls['count']}"

        expected = "x:3:1"
        assert await compute(3) == expected
        assert await compute(value=3) == expected
        assert await compute(3, scale=1) == expected
        assert await compute(3, offset=0, scale=1) == expected
        assert await compute(value=3, label="x", offset=0, scale=1) == expected
        assert calls["count"] == 1
        assert compute.cache_info().hits >= 4

    asyncio.run(scenario())


def test_sync_invalidate_during_inflight_does_not_recache_stale_thread_result():
    cache = AsyncTTLCache(maxsize=8, ttl=60)
    entered = threading.Event()
    release = threading.Event()

    def slow_factory():
        entered.set()
        assert release.wait(timeout=2)
        return "old"

    result = {}
    errors = []

    def worker():
        try:
            result["value"] = cache.get_or_set("target", slow_factory)
        except BaseException as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(timeout=1)
    assert cache.invalidate("target") is True
    release.set()
    thread.join(timeout=2)

    assert not errors
    assert result["value"] == "old"
    calls = {"count": 0}

    def replacement():
        calls["count"] += 1
        return "new"

    assert cache.get_or_set("target", replacement) == "new"
    assert calls["count"] == 1
    assert cache.cache_info().inflight == 0


def test_sync_clear_during_inflight_does_not_recache_any_stale_thread_result():
    cache = AsyncTTLCache(maxsize=8, ttl=60)
    cache.get_or_set("existing", lambda: "keep-before-clear")
    entered = threading.Event()
    release = threading.Event()

    def slow_factory():
        entered.set()
        assert release.wait(timeout=2)
        return "old"

    thread_result = {}
    thread = threading.Thread(
        target=lambda: thread_result.setdefault("value", cache.get_or_set("target", slow_factory))
    )
    thread.start()
    assert entered.wait(timeout=1)
    before = cache.clear()
    assert isinstance(before, CacheInfo)
    assert before.size >= 1
    release.set()
    thread.join(timeout=2)

    assert thread_result["value"] == "old"
    calls = {"target": 0, "existing": 0}
    assert cache.get_or_set("target", lambda: calls.__setitem__("target", calls["target"] + 1) or "new") == "new"
    assert cache.get_or_set("existing", lambda: calls.__setitem__("existing", calls["existing"] + 1) or "new-existing") == "new-existing"
    assert calls == {"target": 1, "existing": 1}
    assert cache.cache_info().inflight == 0


def test_async_single_flight_survives_one_cancelled_waiter_with_other_waiters():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        calls = {"count": 0}
        entered = asyncio.Event()

        async def factory():
            calls["count"] += 1
            entered.set()
            await asyncio.sleep(0.08)
            return "shared"

        leader = asyncio.create_task(cache.aget_or_set("k", factory))
        await asyncio.wait_for(entered.wait(), timeout=1)
        waiters = [asyncio.create_task(cache.aget_or_set("k", factory)) for _ in range(5)]
        await asyncio.sleep(0)
        waiters[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiters[0]

        results = await asyncio.gather(leader, *waiters[1:])
        assert results == ["shared"] * 5
        assert calls["count"] == 1
        assert cache.cache_info().inflight == 0

    asyncio.run(scenario())


def test_lru_recency_is_refreshed_by_concurrent_hits_before_eviction():
    cache = AsyncTTLCache(maxsize=2, ttl=60)
    cache.get_or_set("a", lambda: "A")
    cache.get_or_set("b", lambda: "B")

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.get_or_set("a", lambda: "A-lost")))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert results == ["A"] * 8
    cache.get_or_set("c", lambda: "C")
    assert cache.get_or_set("a", lambda: "A-lost") == "A"

    calls = {"b": 0}

    def new_b():
        calls["b"] += 1
        return "B-new"

    assert cache.get_or_set("b", new_b) == "B-new"
    assert calls["b"] == 1


def test_ttl_expiry_during_same_key_async_contention_starts_one_new_factory():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=0.05)
        calls = {"count": 0}

        async def factory():
            calls["count"] += 1
            await asyncio.sleep(0.02)
            return f"value-{calls['count']}"

        assert await cache.aget_or_set("k", factory) == "value-1"
        await asyncio.sleep(0.07)
        results = await asyncio.gather(*(cache.aget_or_set("k", factory) for _ in range(6)))
        assert results == ["value-2"] * 6
        assert calls["count"] == 2
        assert cache.cache_info().inflight == 0

    asyncio.run(scenario())
