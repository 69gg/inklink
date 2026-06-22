from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import TracebackType

MonotonicClock = Callable[[], float]
AsyncSleep = Callable[[float], Awaitable[None]]


class ProfileLimiter:
    def __init__(
        self,
        *,
        max_concurrency: int,
        rpm: int | None = None,
        monotonic: MonotonicClock = time.monotonic,
        sleep: AsyncSleep = asyncio.sleep,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than 0")
        if rpm is not None and rpm <= 0:
            raise ValueError("rpm must be greater than 0")

        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._min_interval_seconds = None if rpm is None else 60.0 / rpm
        self._monotonic = monotonic
        self._sleep = sleep
        self._rpm_lock = asyncio.Lock()
        self._last_entry_at: float | None = None

    async def __aenter__(self) -> ProfileLimiter:
        await self._semaphore.acquire()
        try:
            await self._wait_for_rate_limit()
        except BaseException:
            self._semaphore.release()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._semaphore.release()

    async def _wait_for_rate_limit(self) -> None:
        if self._min_interval_seconds is None:
            return

        async with self._rpm_lock:
            now = self._monotonic()
            if self._last_entry_at is not None:
                elapsed = now - self._last_entry_at
                delay = self._min_interval_seconds - elapsed
                if delay > 0:
                    await self._sleep(delay)
                    now = self._monotonic()
            self._last_entry_at = now
