from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from inklink.llm.limiter import ProfileLimiter


async def test_max_concurrency_one_prevents_simultaneous_entry() -> None:
    limiter = ProfileLimiter(max_concurrency=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = False

    async def first_worker() -> None:
        async with limiter:
            entered.set()
            await release.wait()

    async def second_worker() -> None:
        nonlocal second_entered
        async with limiter:
            second_entered = True

    first_task = asyncio.create_task(first_worker())
    await entered.wait()

    second_task = asyncio.create_task(second_worker())
    await asyncio.sleep(0)
    assert second_entered is False

    release.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered is True


async def test_rpm_waits_between_entries_without_slowing_test() -> None:
    now = 100.0
    sleep_calls: list[float] = []

    def monotonic() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        sleep_calls.append(delay)
        now += delay

    limiter = ProfileLimiter(max_concurrency=1, rpm=60, monotonic=monotonic, sleep=sleep)

    async with limiter:
        pass

    async with limiter:
        pass

    assert sleep_calls == [pytest.approx(1.0)]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ProfileLimiter(max_concurrency=0), "max_concurrency"),
        (lambda: ProfileLimiter(max_concurrency=-1), "max_concurrency"),
        (lambda: ProfileLimiter(max_concurrency=1, rpm=0), "rpm"),
        (lambda: ProfileLimiter(max_concurrency=1, rpm=-1), "rpm"),
    ],
)
def test_invalid_limiter_config_raises(
    factory: Callable[[], ProfileLimiter],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_injected_sleep_must_be_async() -> None:
    def sleep(_delay: float) -> Awaitable[None]:
        raise AssertionError("not called")

    ProfileLimiter(max_concurrency=1, rpm=60, sleep=sleep)
