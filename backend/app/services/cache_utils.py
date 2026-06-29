"""Simple TTL cache utility for read-only analysis endpoints.

Provides a thread-safe, per-function TTL cache that is suitable for
caching expensive read-only aggregations (calibration, market comparison,
AI evaluation, etc.). The cache is process-local (in-memory).

Usage::

    from app.services.cache_utils import cached_get

    @router.get("/model-calibration")
    @cached_get(ttl_seconds=30)
    def model_calibration():
        with session_scope() as session:
            return compute_calibration(session)

To invalidate all caches (e.g., after recompute), call
``invalidate_all_caches()`` or use the ``invalidate_dashboard_caches``
helper in dashboard.py which calls this internally.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable


# Module-level registry of all cache instances so they can be
# invalidated centrally via invalidate_all_caches().
_CACHE_INSTANCES: list["_TTLCache"] = []
_REGISTRY_LOCK = threading.Lock()


class _TTLCache:
    """Thread-safe single-slot TTL cache."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._value: Any = None
        self._ts: float = 0.0
        self._has_value: bool = False
        self._lock = threading.Lock()

    def get(self) -> Any:
        now = time.monotonic()
        with self._lock:
            if self._has_value and (now - self._ts) < self.ttl_seconds:
                return self._value
            return _MISS

    def set(self, value: Any) -> None:
        with self._lock:
            self._value = value
            self._ts = time.monotonic()
            self._has_value = True

    def invalidate(self) -> None:
        with self._lock:
            self._value = None
            self._has_value = False
            self._ts = 0.0


_MISS = object()


def cached_get(ttl_seconds: float = 30.0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that caches a function's return value for ttl_seconds.

    Suitable for FastAPI GET endpoints that return read-only aggregations.
    Cached values are stored in-memory and shared across all requests in
    the process. The cache is invalidated automatically after the TTL or
    via invalidate_all_caches().
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        cache = _TTLCache(ttl_seconds)
        with _REGISTRY_LOCK:
            _CACHE_INSTANCES.append(cache)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            cached = cache.get()
            if cached is not _MISS:
                return cached
            result = func(*args, **kwargs)
            cache.set(result)
            return result

        # Attach cache instance for per-function invalidation if needed.
        wrapper._ttl_cache = cache  # type: ignore[attr-defined]
        return wrapper

    return decorator


def invalidate_all_caches() -> None:
    """Invalidate every TTL cache registered via @cached_get.

    Called by invalidate_dashboard_caches() after data mutations
    (recompute, snapshot writes, workflow completion, etc.).
    """
    with _REGISTRY_LOCK:
        caches = list(_CACHE_INSTANCES)
    for cache in caches:
        cache.invalidate()
