import asyncio
import inspect
import time

import pytest

from maos_cache import AsyncTTLCache, CacheInfo, async_ttl_cache, ttl_cache


def test_sync_decorator_hits_and_misses():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=60)
    def compute(value):
        calls["count"] += 1
        return value * 2

    assert compute(3) == 6
    assert compute(3) == 6
    assert calls["count"] == 1
    info = compute.cache_info()
    assert info.hits >= 1
    assert info.misses == 1


def test_ttl_expiration_recomputes():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=0.05)
    def compute(value):
        calls["count"] += 1
        return calls["count"]

    assert compute("a") == 1
    assert compute("a") == 1
    time.sleep(0.08)
    assert compute("a") == 2


def test_lru_eviction_removes_least_recently_used_key():
    calls = {"count": 0}

    @ttl_cache(maxsize=2, ttl=60)
    def compute(value):
        calls["count"] += 1
        return f"{value}:{calls['count']}"

    assert compute("a") == "a:1"
    assert compute("b") == "b:2"
    assert compute("a") == "a:1"
    assert compute("c") == "c:3"
    assert compute("b") == "b:4"
    info = compute.cache_info()
    assert info.evictions >= 1
    assert info.size <= 2


def test_invalidate_and_clear():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=60)
    def compute(value):
        calls["count"] += 1
        return calls["count"]

    assert compute("x") == 1
    assert compute("x") == 1
    compute.invalidate("x")
    assert compute("x") == 2
    compute.clear()
    assert compute("x") == 3


def test_exceptions_are_not_cached():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=60)
    def flaky():
        calls["count"] += 1
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            flaky()
    assert calls["count"] == 2


def test_async_decorator_single_flight_for_same_key():
    async def scenario():
        calls = {"count": 0}
        entered = asyncio.Event()

        @async_ttl_cache(maxsize=8, ttl=60)
        async def compute(value):
            calls["count"] += 1
            entered.set()
            await asyncio.sleep(0.05)
            return value * 10

        tasks = [asyncio.create_task(compute(7)) for _ in range(20)]
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert await asyncio.gather(*tasks) == [70] * 20
        assert calls["count"] == 1
        info = compute.cache_info()
        assert info.inflight == 0
        assert info.hits >= 19

    asyncio.run(scenario())


def test_async_exceptions_clear_inflight_and_do_not_cache():
    async def scenario():
        calls = {"count": 0}

        @async_ttl_cache(maxsize=8, ttl=60)
        async def flaky():
            calls["count"] += 1
            await asyncio.sleep(0)
            raise ValueError("bad")

        for _ in range(2):
            with pytest.raises(ValueError):
                await flaky()
            assert flaky.cache_info().inflight == 0
        assert calls["count"] == 2

    asyncio.run(scenario())


def test_direct_cache_get_or_set_sync_and_info_shape():
    cache = AsyncTTLCache(maxsize=2, ttl=60)
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        return "value"

    assert cache.get_or_set("k", factory) == "value"
    assert cache.get_or_set("k", factory) == "value"
    assert calls["count"] == 1
    info = cache.cache_info()
    for field in ("hits", "misses", "evictions", "inflight", "size"):
        assert hasattr(info, field)


def test_public_api_contract_and_cache_info_type():
    cache = AsyncTTLCache(maxsize=4, ttl=60)

    assert inspect.isclass(AsyncTTLCache)
    assert hasattr(cache, "get_or_set")
    assert hasattr(cache, "aget_or_set")
    assert hasattr(cache, "invalidate")
    assert hasattr(cache, "clear")
    assert hasattr(cache, "cache_info")

    info = cache.cache_info()
    assert isinstance(info, CacheInfo)
    assert info.hits == 0
    assert info.misses == 0
    assert info.evictions == 0
    assert info.inflight == 0
    assert info.size == 0


def test_lru_hit_refreshes_recency_exact_key_removed():
    calls = {"count": 0}

    @ttl_cache(maxsize=2, ttl=60)
    def compute(value):
        calls["count"] += 1
        return f"{value}:{calls['count']}"

    assert compute("a") == "a:1"
    assert compute("b") == "b:2"
    assert compute("a") == "a:1"
    assert compute("c") == "c:3"
    assert compute("a") == "a:1"
    assert compute("b") == "b:4"
    info = compute.cache_info()
    assert info.evictions >= 1
    assert info.size == 2


def test_ttl_expiration_does_not_count_as_lru_eviction():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=0.04)
    def compute(value):
        calls["count"] += 1
        return calls["count"]

    assert compute("x") == 1
    time.sleep(0.07)
    assert compute("x") == 2
    info = compute.cache_info()
    assert info.evictions == 0
    assert info.misses == 2


def test_sync_decorator_uses_args_and_kwargs_as_cache_key():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=60)
    def compute(a, *, scale=1, offset=0):
        calls["count"] += 1
        return a * scale + offset

    assert compute(3, scale=2, offset=1) == 7
    assert compute(3, offset=1, scale=2) == 7
    assert compute(3, scale=3, offset=1) == 10
    assert calls["count"] == 2


def test_invalidate_returns_boolean_and_only_removes_target():
    calls = {"count": 0}

    @ttl_cache(maxsize=8, ttl=60)
    def compute(value):
        calls["count"] += 1
        return f"{value}:{calls['count']}"

    assert compute("a") == "a:1"
    assert compute("b") == "b:2"
    assert compute.invalidate("missing") is False
    assert compute.invalidate("a") is True
    assert compute("b") == "b:2"
    assert compute("a") == "a:3"


def test_clear_returns_snapshot_and_resets_stored_entries():
    cache = AsyncTTLCache(maxsize=8, ttl=60)

    assert cache.get_or_set("a", lambda: "A") == "A"
    assert cache.get_or_set("b", lambda: "B") == "B"
    before = cache.clear()
    after = cache.cache_info()

    assert isinstance(before, CacheInfo)
    assert before.size == 2
    assert after.size == 0
    assert after.hits == 0
    assert after.misses == 0


def test_direct_aget_or_set_single_flight_and_stats():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        calls = {"count": 0}

        async def factory():
            calls["count"] += 1
            await asyncio.sleep(0.05)
            return "shared"

        results = await asyncio.gather(
            *[asyncio.create_task(cache.aget_or_set("same", factory)) for _ in range(12)]
        )
        assert results == ["shared"] * 12
        assert calls["count"] == 1
        info = cache.cache_info()
        assert info.misses == 1
        assert info.hits >= 11
        assert info.inflight == 0

    asyncio.run(scenario())


def test_direct_aget_or_set_different_keys_run_independently():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        calls = {"count": 0}

        async def factory(value):
            calls["count"] += 1
            await asyncio.sleep(0.02)
            return value

        results = await asyncio.gather(
            cache.aget_or_set("a", lambda: factory("A")),
            cache.aget_or_set("b", lambda: factory("B")),
            cache.aget_or_set("a", lambda: factory("A2")),
        )
        assert sorted(results) == ["A", "A", "B"]
        assert calls["count"] == 2

    asyncio.run(scenario())


def test_concurrent_async_exception_clears_inflight_and_retries_cleanly():
    async def scenario():
        cache = AsyncTTLCache(maxsize=8, ttl=60)
        calls = {"count": 0}

        async def failing_factory():
            calls["count"] += 1
            await asyncio.sleep(0.03)
            raise RuntimeError("boom")

        tasks = [asyncio.create_task(cache.aget_or_set("bad", failing_factory)) for _ in range(5)]
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


def test_async_decorator_preserves_function_metadata_and_cache_controls():
    @async_ttl_cache(maxsize=8, ttl=60)
    async def compute(value):
        """Useful docstring."""
        return value * 2

    assert compute.__name__ == "compute"
    assert "Useful docstring" in (compute.__doc__ or "")
    assert hasattr(compute, "cache_info")
    assert hasattr(compute, "invalidate")
    assert hasattr(compute, "clear")
