"""Per-token concurrency guards shared across services."""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict, deque
from dataclasses import dataclass

from harnyx_commons.errors import ConcurrencyLimitError
from harnyx_commons.tools.dto import ToolInvocationRequest


@dataclass(slots=True)
class _AsyncWaiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]
    granted: bool = False


class TokenSemaphore:
    """Lightweight counting semaphore for access tokens."""

    def __init__(self, max_parallel_calls: int = 1) -> None:
        if max_parallel_calls <= 0:
            raise ValueError("max_parallel_calls must be positive")
        self._max_parallel_calls = max_parallel_calls
        self._in_flight: defaultdict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._waiters: defaultdict[str, deque[_AsyncWaiter]] = defaultdict(deque)

    def acquire(self, token: str) -> None:
        """Reserve a permit for the supplied token or raise when exhausted."""
        with self._lock:
            if not self._try_acquire_locked(token):
                raise ConcurrencyLimitError(
                    f"token {token!r} exceeds {self._max_parallel_calls} concurrent calls",
                )

    async def acquire_async(self, token: str) -> None:
        """Reserve a permit for the supplied token, waiting until one becomes available."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._try_acquire_locked(token):
                return
            waiter = _AsyncWaiter(loop=loop, future=loop.create_future())
            self._waiters[token].append(waiter)
        try:
            await waiter.future
        except asyncio.CancelledError:
            with self._lock:
                if waiter.granted:
                    self._release_locked(token)
                else:
                    self._discard_waiter_locked(token, waiter)
            raise

    def release(self, token: str) -> None:
        """Release a previously acquired permit."""
        with self._lock:
            self._release_locked(token)

    def in_flight(self, token: str) -> int:
        """Return the number of active calls for a token."""
        with self._lock:
            return self._in_flight.get(token, 0)

    def _try_acquire_locked(self, token: str) -> bool:
        current = self._in_flight.get(token, 0)
        if current >= self._max_parallel_calls:
            return False
        self._in_flight[token] = current + 1
        return True

    def _release_locked(self, token: str) -> None:
        current = self._in_flight.get(token)
        if current is None or current == 0:
            raise RuntimeError(f"token {token!r} has no active permits to release")
        remaining = current - 1
        waiter = self._pop_next_waiter_locked(token)
        if waiter is None:
            if remaining == 0:
                del self._in_flight[token]
            else:
                self._in_flight[token] = remaining
            return
        self._in_flight[token] = remaining + 1
        waiter.granted = True
        waiter.loop.call_soon_threadsafe(self._resolve_waiter, waiter.future)

    def _pop_next_waiter_locked(self, token: str) -> _AsyncWaiter | None:
        waiters = self._waiters.get(token)
        if waiters is None:
            return None
        while waiters:
            waiter = waiters.popleft()
            if waiter.future.cancelled():
                continue
            if not waiters:
                del self._waiters[token]
            return waiter
        del self._waiters[token]
        return None

    def _discard_waiter_locked(self, token: str, waiter: _AsyncWaiter) -> None:
        waiters = self._waiters.get(token)
        if waiters is None:
            return
        self._waiters[token] = deque(queued for queued in waiters if queued is not waiter)
        if not self._waiters[token]:
            del self._waiters[token]

    @staticmethod
    def _resolve_waiter(future: asyncio.Future[None]) -> None:
        if not future.done():
            future.set_result(None)


@dataclass(frozen=True, slots=True)
class ToolConcurrencyLimits:
    max_parallel_calls: int

    def __post_init__(self) -> None:
        if self.max_parallel_calls <= 0:
            raise ValueError("max_parallel_calls must be positive")


DEFAULT_TOOL_CONCURRENCY_LIMITS = ToolConcurrencyLimits(max_parallel_calls=20)


def max_parallel_provider_tool_calls(
    limits: ToolConcurrencyLimits = DEFAULT_TOOL_CONCURRENCY_LIMITS,
) -> int:
    """Return the scalar upper bound for a miner script tool session."""
    return limits.max_parallel_calls


class ToolConcurrencyLimiter:
    """Per-token tool concurrency for a miner script session."""

    def __init__(self, limits: ToolConcurrencyLimits = DEFAULT_TOOL_CONCURRENCY_LIMITS) -> None:
        self._semaphore = TokenSemaphore(max_parallel_calls=limits.max_parallel_calls)

    def acquire(self, invocation: ToolInvocationRequest) -> None:
        self._semaphore_for(invocation).acquire(invocation.token)

    async def acquire_async(self, invocation: ToolInvocationRequest) -> None:
        await self._semaphore_for(invocation).acquire_async(invocation.token)

    def release(self, invocation: ToolInvocationRequest) -> None:
        self._semaphore_for(invocation).release(invocation.token)

    def in_flight(self, invocation: ToolInvocationRequest) -> int:
        return self._semaphore_for(invocation).in_flight(invocation.token)

    def _semaphore_for(self, invocation: ToolInvocationRequest) -> TokenSemaphore:
        return self._semaphore


__all__ = [
    "DEFAULT_TOOL_CONCURRENCY_LIMITS",
    "TokenSemaphore",
    "ToolConcurrencyLimiter",
    "ToolConcurrencyLimits",
    "max_parallel_provider_tool_calls",
]
