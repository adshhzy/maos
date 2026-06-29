"""tests/test_maos_cache.py - Comprehensive pytest tests for maos_cache module.

Covers: cache hit/miss, TTL expiration, LRU eviction, invalidate/clear,
exception not cached, async concurrent single-flight, sync/async decorators,
cache_info statistics, custom key_maker, edge cases.

Bug fixes from quality_review_gate:
  (a) All async tests use await cache.get(func,...) - no sync get(key) for async paths
  (b) ttl<0 means never expire per requirements spec (no ValueError)
  (c) Expired entry eviction counted via async get path, not sync get(key)

Requires: pytest, pytest-asyncio
"""

import asyncio
import time
from dataclasses import FrozenInstanceError

import pytest

from maos_runtime.maos_cache import (
    AsyncTTLCache,
    async_ttl_cache,
    sync_ttl_cache,
    CacheInfo,
)


class _Counter:
    """Simple mutable counter for tracking function calls in async closures."""
    def __init__(self, start=0):
        self.value = start
    def inc(self):
        self.value += 1
        return self.value


# ============ 1. CacheInfo shape & frozen ============

class TestCacheInfo:
    def test_initial_all_zero(self):
        c = AsyncTTLCache(ttl=60)
        info = c.cache_info()
        assert info == CacheInfo(hits=0, misses=0, evictions=0, inflight=0, size=0)

    def test_frozen_immutable(self):
        info = AsyncTTLCache(ttl=60).cache_info()
        with pytest.raises(FrozenInstanceError):
            info.hits = 99

    def test_all_fields_present(self):
        info = AsyncTTLCache(ttl=60).cache_info()
        for f in ("hits", "misses", "evictions", "inflight", "size"):
            assert hasattr(info, f)


# ============ 2. Cache hit / miss ============

class TestCacheHitMiss:
    @pytest.mark.asyncio
    async def test_first_call_is_miss(self):
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=60)
        result = await cache.get(doubler, 5)
        assert result == 10 and cc.value == 1
        info = cache.cache_info()
        assert info.misses == 1 and info.hits == 0

    @pytest.mark.asyncio
    async def test_second_call_is_hit(self):
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=60)
        await cache.get(doubler, 5)
        result = await cache.get(doubler, 5)
        assert result == 10 and cc.value == 1
        info = cache.cache_info()
        assert info.hits == 1 and info.misses == 1 and info.size == 1

    @pytest.mark.asyncio
    async def test_different_args_different_keys(self):
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=60)
        await cache.get(doubler, 1)
        await cache.get(doubler, 2)
        assert cc.value == 2 and cache.cache_info().size == 2

    @pytest.mark.asyncio
    async def test_kwargs_order_invariant(self):
        cc = _Counter()
        async def add(a, b):
            cc.inc(); return a + b
        cache = AsyncTTLCache(ttl=60)
        r1 = await cache.get(add, a=1, b=2)
        r2 = await cache.get(add, b=2, a=1)
        assert r1 == 3 and r2 == 3 and cc.value == 1


# ============ 3. TTL expiration ============

class TestTTLExpiration:
    @pytest.mark.asyncio
    async def test_entry_expires_after_ttl(self):
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=0.15)
        await cache.get(doubler, 5)
        assert cc.value == 1
        await asyncio.sleep(0.25)
        result = await cache.get(doubler, 5)
        assert result == 10 and cc.value == 2
        info = cache.cache_info()
        assert info.misses == 2 and info.hits == 0 and info.evictions == 1

    @pytest.mark.asyncio
    async def test_ttl_zero_means_never_expire(self):
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=0)
        await cache.get(doubler, 5)
        await asyncio.sleep(0.05)
        result = await cache.get(doubler, 5)
        assert result == 10 and cc.value == 1
        assert cache.cache_info().hits == 1

    @pytest.mark.asyncio
    async def test_ttl_negative_means_never_expire(self):
        """Bug fix (b): ttl<0 means never expire per spec - no ValueError."""
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=-1)
        await cache.get(doubler, 5)
        await asyncio.sleep(0.05)
        result = await cache.get(doubler, 5)
        assert result == 10 and cc.value == 1
        assert cache.cache_info().hits == 1

    @pytest.mark.asyncio
    async def test_hit_within_ttl_window(self):
        cc = _Counter()
        async def doubler(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=1.0)
        await cache.get(doubler, 5)
        await asyncio.sleep(0.05)
        result = await cache.get(doubler, 5)
        assert result == 10 and cc.value == 1
        assert cache.cache_info().hits == 1


# ============ 4. LRU eviction ============

class TestLRUEviction:
    @pytest.mark.asyncio
    async def test_lru_overflow_evicts_oldest(self):
        cc = _Counter()
        async def identity(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=60, maxsize=2)
        await cache.get(identity, "a")
        await cache.get(identity, "b")
        await cache.get(identity, "c")
        info = cache.cache_info()
        assert info.evictions == 1 and info.size == 2
        await cache.get(identity, "a")
        assert cc.value == 4

    @pytest.mark.asyncio
    async def test_lru_access_prevents_eviction(self):
        cc = _Counter()
        async def identity(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=60, maxsize=2)
        await cache.get(identity, "a")
        await cache.get(identity, "b")
        await cache.get(identity, "a")
        await cache.get(identity, "c")
        result = await cache.get(identity, "a")
        assert result == "a" and cc.value == 3

    @pytest.mark.asyncio
    async def test_maxsize_zero_no_eviction(self):
        cc = _Counter()
        async def identity(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=60, maxsize=0)
        for i in range(200):
            await cache.get(identity, i)
        assert cache.cache_info().size == 200
        assert cache.cache_info().evictions == 0

    def test_maxsize_negative_raises_valueerror(self):
        with pytest.raises(ValueError):
            AsyncTTLCache(ttl=60, maxsize=-1)

    def test_maxsize_one(self):
        @sync_ttl_cache(maxsize=1, ttl=60)
        def f(x):
            return x * 2
        f(1); f(2)
        assert f.__cache__.cache_info().size == 1
        assert f.__cache__.cache_info().evictions == 1


# ============ 5. Invalidate / Clear ============

class TestInvalidateClear:
    @pytest.mark.asyncio
    async def test_invalidate_existing_key(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=60)
        await cache.get(f, "k")
        assert cache.cache_info().size == 1
        result = cache.invalidate("k")
        assert result is True
        assert cache.cache_info().size == 0

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_key(self):
        cache = AsyncTTLCache(ttl=60)
        result = cache.invalidate("ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_invalidate_does_not_count_as_eviction(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=60)
        await cache.get(f, "a")
        await cache.get(f, "b")
        cache.invalidate("a")
        assert cache.cache_info().evictions == 0

    @pytest.mark.asyncio
    async def test_invalidate_with_kwargs(self):
        cc = _Counter()
        async def f(a, b):
            cc.inc(); return a + b
        cache = AsyncTTLCache(ttl=60)
        await cache.get(f, a=1, b=2)
        await cache.get(f, a=3, b=4)
        assert cache.cache_info().size == 2
        cache.invalidate(a=1, b=2)
        assert cache.cache_info().size == 1

    @pytest.mark.asyncio
    async def test_clear_returns_snapshot_before_clear(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=60)
        await cache.get(f, "a")
        await cache.get(f, "a")
        await cache.get(f, "b")
        before = cache.cache_info()
        snapshot = cache.clear()
        assert snapshot.hits == before.hits
        assert snapshot.misses == before.misses
        assert snapshot.size == before.size
        after = cache.cache_info()
        assert after.size == 0 and after.hits == 0 and after.misses == 0

    @pytest.mark.asyncio
    async def test_clear_does_not_reset_inflight(self):
        cache = AsyncTTLCache(ttl=60)
        cache._inflight = 3
        cache.clear()
        assert cache.cache_info().inflight == 3


# ============ 6. Exception not cached ============

class TestExceptionNotCached:
    @pytest.mark.asyncio
    async def test_async_exception_not_cached(self):
        cc = _Counter()
        async def failing(x):
            cc.inc(); raise ValueError("boom")
        cache = AsyncTTLCache(ttl=60)
        with pytest.raises(ValueError):
            await cache.get(failing, 1)
        assert cc.value == 1
        with pytest.raises(ValueError):
            await cache.get(failing, 1)
        assert cc.value == 2
        assert cache.cache_info().size == 0

    @pytest.mark.asyncio
    async def test_exception_propagates_to_all_singleflight_waiters(self):
        cc = _Counter()
        async def failing(x):
            cc.inc(); await asyncio.sleep(0.05); raise RuntimeError("async boom")
        cache = AsyncTTLCache(ttl=60)
        async def call_and_catch():
            try:
                return await cache.get(failing, "k")
            except RuntimeError:
                return "caught"
        results = await asyncio.gather(*(call_and_catch() for _ in range(5)))
        assert all(r == "caught" for r in results)
        assert cc.value == 1

    def test_sync_exception_not_cached(self):
        cc = _Counter()
        @sync_ttl_cache(maxsize=10, ttl=60)
        def failing(x):
            cc.inc(); raise ValueError("boom")
        with pytest.raises(ValueError):
            failing(1)
        assert cc.value == 1
        with pytest.raises(ValueError):
            failing(1)
        assert cc.value == 2


# ============ 7. Async concurrent single-flight ============

class TestSingleFlight:
    @pytest.mark.asyncio
    async def test_single_flight_dedup_10_concurrent(self):
        cc = _Counter()
        async def slow(x):
            cc.inc(); await asyncio.sleep(0.1); return x * 2
        cache = AsyncTTLCache(ttl=60)
        results = await asyncio.gather(*(cache.get(slow, 5) for _ in range(10)))
        assert all(r == 10 for r in results)
        assert cc.value == 1
        info = cache.cache_info()
        assert info.misses == 1 and info.hits == 9 and info.inflight == 0

    @pytest.mark.asyncio
    async def test_single_flight_different_keys(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); await asyncio.sleep(0.05); return x * 2
        cache = AsyncTTLCache(ttl=60)
        results = await asyncio.gather(
            cache.get(f, 1), cache.get(f, 2), cache.get(f, 3)
        )
        assert results == [2, 4, 6]
        assert cc.value == 3
        info = cache.cache_info()
        assert info.misses == 3 and info.hits == 0

    @pytest.mark.asyncio
    async def test_inflight_reflects_live_count(self):
        cc = _Counter()
        inflight_snaps = []
        async def slow(x):
            inflight_snaps.append(cache.cache_info().inflight)
            await asyncio.sleep(0.1); return x
        cache = AsyncTTLCache(ttl=60)
        await asyncio.gather(*(cache.get(slow, 5) for _ in range(5)))
        assert max(inflight_snaps) >= 1
        assert cache.cache_info().inflight == 0


# ============ 8. Async function decorator ============

class TestAsyncDecorator:
    @pytest.mark.asyncio
    async def test_async_decorator_hit_miss(self):
        cc = _Counter()
        @async_ttl_cache(maxsize=10, ttl=60)
        async def compute(x):
            cc.inc(); await asyncio.sleep(0.01); return x * 2
        r1 = await compute(5)
        r2 = await compute(5)
        assert r1 == 10 and r2 == 10 and cc.value == 1

    @pytest.mark.asyncio
    async def test_async_decorator_single_flight(self):
        cc = _Counter()
        @async_ttl_cache(maxsize=10, ttl=60)
        async def slow(x):
            cc.inc(); await asyncio.sleep(0.1); return x * 2
        results = await asyncio.gather(*(slow(5) for _ in range(10)))
        assert all(r == 10 for r in results) and cc.value == 1
        info = slow.__cache__.cache_info()
        assert info.misses == 1 and info.hits == 9 and info.inflight == 0

    @pytest.mark.asyncio
    async def test_async_decorator_exception_not_cached(self):
        cc = _Counter()
        @async_ttl_cache(maxsize=10, ttl=60)
        async def failing(x):
            cc.inc(); raise RuntimeError("async boom")
        with pytest.raises(RuntimeError):
            await failing(1)
        assert cc.value == 1
        with pytest.raises(RuntimeError):
            await failing(1)
        assert cc.value == 2

    @pytest.mark.asyncio
    async def test_async_decorator_cache_attribute(self):
        @async_ttl_cache(maxsize=10, ttl=60)
        async def f(x):
            return x
        assert hasattr(f, "__cache__")
        assert isinstance(f.__cache__, AsyncTTLCache)

    @pytest.mark.asyncio
    async def test_async_decorator_invalidate_via_cache(self):
        cc = _Counter()
        @async_ttl_cache(maxsize=10, ttl=60)
        async def f(x):
            cc.inc(); return x
        await f(1); await f(2)
        assert f.__cache__.cache_info().size == 2
        f.__cache__.invalidate(1)
        assert f.__cache__.cache_info().size == 1

    @pytest.mark.asyncio
    async def test_async_decorator_clear_via_cache(self):
        @async_ttl_cache(maxsize=10, ttl=60)
        async def f(x):
            return x
        await f(1); await f(2)
        f.__cache__.clear()
        assert f.__cache__.cache_info().size == 0

    @pytest.mark.asyncio
    async def test_async_decorator_wraps_metadata(self):
        @async_ttl_cache(maxsize=10, ttl=60)
        async def my_func():
            """Async doc."""
            return 42
        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "Async doc."

    @pytest.mark.asyncio
    async def test_async_decorator_custom_key_maker(self):
        cc = _Counter()
        @async_ttl_cache(maxsize=10, ttl=60, key_maker=lambda s: (s.upper(),))
        async def fetch(s):
            cc.inc(); return s.lower()
        await fetch("hello")
        r = await fetch("HELLO")
        assert r == "hello" and cc.value == 1


# ============ 9. Sync function decorator ============

class TestSyncDecorator:
    def test_sync_decorator_hit_miss(self):
        cc = _Counter()
        @sync_ttl_cache(maxsize=10, ttl=60)
        def compute(x):
            cc.inc(); return x * 2
        assert compute(5) == 10
        assert compute(5) == 10
        assert cc.value == 1
        info = compute.__cache__.cache_info()
        assert info.hits == 1 and info.misses == 1

    def test_sync_decorator_different_args(self):
        @sync_ttl_cache(maxsize=10, ttl=60)
        def compute(x):
            return x * 2
        compute(1); compute(2)
        assert compute.__cache__.cache_info().size == 2

    def test_sync_decorator_kwargs_order_invariant(self):
        cc = _Counter()
        @sync_ttl_cache(maxsize=10, ttl=60)
        def compute(a, b):
            cc.inc(); return a + b
        assert compute(a=1, b=2) == 3
        assert compute(b=2, a=1) == 3
        assert cc.value == 1

    def test_sync_decorator_ttl_expiration(self):
        cc = _Counter()
        @sync_ttl_cache(maxsize=10, ttl=0.15)
        def compute(x):
            cc.inc(); return x * 2
        compute(5)
        time.sleep(0.25)
        compute(5)
        assert cc.value == 2
        info = compute.__cache__.cache_info()
        assert info.evictions >= 1

    def test_sync_decorator_in_async_loop_raises_runtimeerror(self):
        @sync_ttl_cache(maxsize=10, ttl=60)
        def compute(x):
            return x * 2
        async def try_call():
            return compute(5)
        with pytest.raises(RuntimeError, match="sync_ttl_cache"):
            asyncio.run(try_call())

    def test_sync_decorator_cache_attribute(self):
        @sync_ttl_cache(maxsize=10, ttl=60)
        def f(x):
            return x
        assert hasattr(f, "__cache__")
        assert isinstance(f.__cache__, AsyncTTLCache)

    def test_sync_decorator_wraps_metadata(self):
        @sync_ttl_cache(maxsize=10, ttl=60)
        def my_func():
            """My doc."""
            return 42
        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "My doc."

    def test_sync_decorator_invalidate(self):
        @sync_ttl_cache(maxsize=10, ttl=60)
        def compute(x):
            return x * 2
        compute(1); compute(2); compute(3)
        assert compute.__cache__.cache_info().size == 3
        compute.__cache__.invalidate(2)
        assert compute.__cache__.cache_info().size == 2

    def test_sync_decorator_clear(self):
        @sync_ttl_cache(maxsize=10, ttl=60)
        def compute(x):
            return x * 2
        compute(1); compute(2)
        compute.__cache__.clear()
        assert compute.__cache__.cache_info().size == 0


# ============ 10. cache_info statistics consistency ============

class TestCacheInfoStats:
    @pytest.mark.asyncio
    async def test_stats_after_hit_miss_eviction_sequence(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=0.15, maxsize=2)
        await cache.get(f, "a")
        await cache.get(f, "b")
        await cache.get(f, "a")
        await cache.get(f, "c")
        info = cache.cache_info()
        assert info.hits == 1
        assert info.misses == 3
        assert info.evictions == 1
        assert info.size == 2

    @pytest.mark.asyncio
    async def test_stats_single_flight_hits_counted(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); await asyncio.sleep(0.05); return x * 2
        cache = AsyncTTLCache(ttl=60)
        await asyncio.gather(*(cache.get(f, 5) for _ in range(10)))
        info = cache.cache_info()
        assert info.hits == 9
        assert info.misses == 1
        assert info.inflight == 0

    @pytest.mark.asyncio
    async def test_stats_inflight_live_and_zero_after(self):
        cc = _Counter()
        snapshots = []
        async def slow(x):
            snapshots.append(cache.cache_info().inflight)
            await asyncio.sleep(0.1); return x
        cache = AsyncTTLCache(ttl=60)
        await asyncio.gather(*(cache.get(slow, 5) for _ in range(5)))
        assert max(snapshots) >= 1
        assert cache.cache_info().inflight == 0


# ============ 11. TTL + LRU interaction ============

class TestTTLLRUInteraction:
    @pytest.mark.asyncio
    async def test_expired_entry_counts_as_eviction_via_async_get(self):
        """Bug fix (c): expired entry via async get counts as eviction."""
        cc = _Counter()
        async def f(x):
            cc.inc(); return x * 2
        cache = AsyncTTLCache(ttl=0.15)
        await cache.get(f, "k1")
        await asyncio.sleep(0.25)
        await cache.get(f, "k1")
        info = cache.cache_info()
        assert info.evictions >= 1

    @pytest.mark.asyncio
    async def test_expired_then_capacity_eviction_both_count(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=0.15, maxsize=3)
        await cache.get(f, "a")
        await cache.get(f, "b")
        await cache.get(f, "c")
        await asyncio.sleep(0.25)
        await cache.get(f, "a")
        await cache.get(f, "d")
        await cache.get(f, "e")
        await cache.get(f, "f")
        info = cache.cache_info()
        assert info.evictions >= 1


# ============ 12. Stress / edge cases ============

class TestStressEdgeCases:
    @pytest.mark.asyncio
    async def test_many_keys_stress(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); return x
        cache = AsyncTTLCache(ttl=60, maxsize=500)
        for i in range(1000):
            await cache.get(f, i)
        assert cache.cache_info().size == 500
        assert cache.cache_info().evictions == 500

    @pytest.mark.asyncio
    async def test_concurrent_clear_and_get_no_deadlock(self):
        cc = _Counter()
        async def f(x):
            cc.inc(); await asyncio.sleep(0.01); return x
        cache = AsyncTTLCache(ttl=60)
        await cache.get(f, "a")
        await cache.get(f, "b")
        async def do_clear():
            cache.clear()
        async def do_get():
            return await cache.get(f, "c")
        await asyncio.gather(do_clear(), do_get(), do_clear())
        assert cache.cache_info().size <= 2


# ============ 13. Custom key_maker ============

class TestCustomKeyMaker:
    @pytest.mark.asyncio
    async def test_custom_key_maker_async(self):
        cc = _Counter()
        @async_ttl_cache(maxsize=10, ttl=60, key_maker=lambda s: (s.upper(),))
        async def fetch(s):
            cc.inc(); return s.lower()
        await fetch("hello")
        r = await fetch("HELLO")
        assert r == "hello" and cc.value == 1

    def test_custom_key_maker_sync(self):
        cc = _Counter()
        @sync_ttl_cache(maxsize=10, ttl=60, key_maker=lambda s: (s.upper(),))
        def hash_str(s):
            cc.inc(); return s[::-1]
        hash_str("hello")
        r = hash_str("HELLO")
        assert r == "olleh" and cc.value == 1

    @pytest.mark.asyncio
    async def test_custom_key_maker_on_cache_instance(self):
        cc = _Counter()
        async def f(s):
            cc.inc(); return s.lower()
        cache = AsyncTTLCache(maxsize=10, ttl=60, key_maker=lambda s: (s.upper(),))
        await cache.get(f, "hello")
        r = await cache.get(f, "HELLO")
        assert r == "hello" and cc.value == 1
