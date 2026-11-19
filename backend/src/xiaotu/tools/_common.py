from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Hashable
from datetime import datetime, timezone
from typing import Generic, TypeVar


def clean(value: object, limit: int = 100) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:limit]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ToolError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


T = TypeVar("T")


class AsyncCache(Generic[T]):
    """Coalesce reads without cancelling shared provider work when a caller disconnects."""

    def __init__(self, max_entries: int = 120):
        self._items: dict[Hashable, tuple[float, asyncio.Task[T]]] = {}
        self._tasks: set[asyncio.Task[T]] = set()
        self._max = max_entries

    async def get(self, key: Hashable, ttl: float, load: Callable[[], Awaitable[T]]) -> T:
        entry = self._items.get(key)
        if entry and (time.monotonic() < entry[0] or not entry[1].done()):
            return await asyncio.shield(entry[1])

        async def invoke() -> T:
            return await load()

        task = asyncio.create_task(invoke())
        self._tasks.add(task)
        if key not in self._items and len(self._items) >= self._max:
            # Don't evict an in-flight read (that could duplicate an upstream request).
            old = next((k for k, v in self._items.items() if v[1].done()), None)
            if old is not None:
                del self._items[old]
        if key in self._items or len(self._items) < self._max:
            self._items[key] = (time.monotonic() + ttl, task)

        def finished(done: asyncio.Task[T]) -> None:
            self._tasks.discard(done)
            failed = done.cancelled() or done.exception() is not None
            current = self._items.get(key)
            if failed and current and current[1] is done:
                self._items.pop(key, None)

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        self._items.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
