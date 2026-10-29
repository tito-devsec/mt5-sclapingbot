"""Bounded retry with exponential backoff, and a simple rate limiter."""

from __future__ import annotations

import time
from collections.abc import Callable


def retry[T](
    fn: Callable[[], T],
    *,
    attempts: int,
    retry_on: tuple[type[BaseException], ...],
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` up to ``attempts`` times. The last exception propagates."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for i in range(attempts):
        try:
            return fn()
        except retry_on:
            if i == attempts - 1:
                raise
            sleep(min(max_delay, base_delay * (2**i)))
    raise AssertionError("unreachable")


class RateLimiter:
    """Enforces a minimum interval between calls."""

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now
