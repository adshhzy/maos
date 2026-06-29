"""maos_cache.py - AsyncTTLCache with TTL, LRU, single-flight, and decorator support.

Provides TTL expiration, LRU eviction, asyncio single-flight dedup,
sync/async cache decorators, exception not cached, cache_info stats.
Only uses Python standard library.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["AsyncTTLCache", "async_ttl_cache", "sync_ttl_cache", "CacheInfo"]


@dataclass(frozen=True)
class CacheInfo:
    """Cache statistics snapshot (immutable dataclass)."""
    hits: int
    misses: int
    evictions: int
    inflight: int
    size: int


class _Node:
    __slots__ = ("key", "value", "expire_at", "prev", "next")

    def __init__(self, key: tuple, value: Any, expire_at: float) -> None:
        self.key: tuple = key
        self.value: Any = value
        self.expire_at: float = expire_at
        self.prev: _Node | None = None
        self.next: _Node | None = None


class AsyncTTLCache:
    __slots__ = (
        "_maxsize", "_ttl", "_cache", "_lock_map", "_key_maker",
        "_hits", "_misses", "_evictions", "_inflight",
        "_head", "_tail",
    )

    def __init__(
        self,
        maxsize: int = 128,
        ttl: float = 300.0,
        key_maker: Callable | None = None,
    ) -> None:
        if maxsize < 0:
            raise ValueError(f"maxsize must be >= 0, got {maxsize}")
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        self._key_maker: Callable | None = key_maker
        self._cache: OrderedDict[tuple, _Node] = OrderedDict()
        self._lock_map: dict[tuple, asyncio.Future[Any]] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._inflight: int = 0
        self._head: _Node | None = None
        self._tail: _Node | None = None

    def _make_key(self, *args, **kwargs) -> tuple:
        if self._key_maker is not None:
            return self._key_maker(*args, **kwargs)
        return tuple(args) + tuple(sorted(kwargs.items()))

    def _is_expired(self, node: _Node) -> bool:
        if self._ttl <= 0:
            return False
        return time.monotonic() >= node.expire_at

    def _add_to_head(self, node: _Node) -> None:
        node.prev = None
        node.next = self._head
        if self._head is not None:
            self._head.prev = node
        self._head = node
        if self._tail is None:
            self._tail = node

    def _remove_node(self, node: _Node) -> None:
        prev_node = node.prev
        next_node = node.next
        if prev_node is not None:
            prev_node.next = next_node
        else:
            self._head = next_node
        if next_node is not None:
            next_node.prev = prev_node
        else:
            self._tail = prev_node
        node.prev = None
        node.next = None

    def _move_to_head(self, node: _Node) -> None:
        self._remove_node(node)
        self._add_to_head(node)

    def _pop_tail(self) -> _Node | None:
        tail = self._tail
        if tail is not None:
            self._remove_node(tail)
        return tail

    def _evict_if_needed(self) -> None:
        if self._maxsize > 0 and len(self._cache) >= self._maxsize:
            tail = self._pop_tail()
            if tail is not None:
                del self._cache[tail.key]
                self._evictions += 1

    def _set(self, key: tuple, value: Any) -> None:
        node = self._cache.get(key)
        if node is not None:
            node.value = value
            node.expire_at = (time.monotonic() + self._ttl) if self._ttl > 0 else float("inf")
            self._move_to_head(node)
        else:
            self._evict_if_needed()
            expire_at = (time.monotonic() + self._ttl) if self._ttl > 0 else float("inf")
            new_node = _Node(key=key, value=value, expire_at=expire_at)
            self._cache[key] = new_node
            self._add_to_head(new_node)

    async def get(self, func: Callable, *args, **kwargs) -> Any:
        key = self._make_key(*args, **kwargs)

        # 1. Check cache
        node = self._cache.get(key)
        if node is not None:
            if not self._is_expired(node):
                self._hits += 1
                self._move_to_head(node)
                return node.value
            self._remove_node(node)
            del self._cache[key]
            self._evictions += 1

        # 2. Check inflight (single-flight dedup)
        future = self._lock_map.get(key)
        if future is not None:
            self._hits += 1
            return await future

        # 3. Execute
        self._misses += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._lock_map[key] = future
        self._inflight += 1
        try:
            result = await func(*args, **kwargs)
            self._set(key, result)
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            del self._lock_map[key]
            self._inflight -= 1

    def invalidate(self, *args, **kwargs) -> bool:
        key = self._make_key(*args, **kwargs)
        node = self._cache.get(key)
        if node is not None:
            self._remove_node(node)
            del self._cache[key]
            return True
        return False

    def clear(self) -> CacheInfo:
        snapshot = self.cache_info()
        self._cache.clear()
        self._head = None
        self._tail = None
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        return snapshot

    def cache_info(self) -> CacheInfo:
        return CacheInfo(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            inflight=self._inflight,
            size=len(self._cache),
        )


def async_ttl_cache(
    maxsize: int = 128,
    ttl: float = 300.0,
    key_maker: Callable | None = None,
) -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        cache = AsyncTTLCache(maxsize=maxsize, ttl=ttl, key_maker=key_maker)
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            return await cache.get(func, *args, **kwargs)
        wrapper.__cache__ = cache
        return wrapper
    return decorator

def sync_ttl_cache(
    maxsize: int = 128,
    ttl: float = 300.0,
    key_maker: Callable | None = None,
) -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        cache = AsyncTTLCache(maxsize=maxsize, ttl=ttl, key_maker=key_maker)
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "sync_ttl_cache wrapper called inside async loop; "
                    "use async_ttl_cache instead."
                )
            key = cache._make_key(*args, **kwargs)
            node = cache._cache.get(key)
            if node is not None:
                if not cache._is_expired(node):
                    cache._hits += 1
                    cache._move_to_head(node)
                    return node.value
                cache._remove_node(node)
                del cache._cache[key]
                cache._evictions += 1
            cache._misses += 1
            try:
                result = func(*args, **kwargs)
            except Exception:
                raise
            cache._set(key, result)
            return result
        wrapper.__cache__ = cache
        return wrapper
    return decorator
