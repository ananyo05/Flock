"""In-memory TTL cache with age tracking for Urja API."""

import asyncio
import time
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class CacheEntry(Generic[T]):
    """Container holding cached value and metadata timestamps."""

    def __init__(self, value: T, ttl: float, created_at: float | None = None):
        self.value = value
        self.ttl = ttl
        self.created_at = created_at if created_at is not None else time.time()

    @property
    def age(self) -> float:
        """Age of the cache entry in seconds."""
        return max(0.0, time.time() - self.created_at)

    @property
    def is_expired(self) -> bool:
        """Whether the cache entry has exceeded its time-to-live."""
        return self.age > self.ttl


class TTLCache:
    """Thread-safe / coroutine-safe in-memory cache supporting TTL and age queries."""

    def __init__(self, default_ttl: float = 900.0):
        self.default_ttl = default_ttl
        self._store: dict[str, CacheEntry[Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[Any | None, float | None]:
        """Retrieve value and age in seconds if not expired.

        Returns: (value, age_seconds) or (None, None) if missing or expired.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, None
            if entry.is_expired:
                del self._store[key]
                return None, None
            return entry.value, entry.age

    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store value with specified or default TTL."""
        effective_ttl = ttl if ttl is not None else self.default_ttl
        async with self._lock:
            self._store[key] = CacheEntry(value=value, ttl=effective_ttl)

    async def delete(self, key: str) -> None:
        """Remove specific key from cache."""
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self, prefix: str | None = None) -> int:
        """Clear all entries or entries matching prefix. Returns count of deleted keys."""
        async with self._lock:
            if prefix is None:
                count = len(self._store)
                self._store.clear()
                return count

            keys_to_delete = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_delete:
                del self._store[k]
            return len(keys_to_delete)
