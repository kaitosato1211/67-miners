"""Background worker for validator platform registration refresh."""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from harnyx_validator.application.status import StatusProvider

logger = logging.getLogger("harnyx_validator.registration_worker")

DEFAULT_REFRESH_INTERVAL_SECONDS = 60.0
DEFAULT_INITIAL_FAILURE_DELAY_SECONDS = 5.0
DEFAULT_JITTER_RATIO = 0.2


@dataclass(slots=True)
class RegistrationRefreshWorker:
    registration_refresh: Callable[[], None]
    status_provider: StatusProvider
    refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS
    initial_failure_delay_seconds: float = DEFAULT_INITIAL_FAILURE_DELAY_SECONDS
    jitter_ratio: float = DEFAULT_JITTER_RATIO
    random_value: Callable[[], float] = random.random
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _next_failure_delay_seconds: float = field(init=False, repr=False)

    worker_name = "validator-registration-refresh-worker"

    def __post_init__(self) -> None:
        if self.refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        if self.initial_failure_delay_seconds <= 0:
            raise ValueError("initial_failure_delay_seconds must be positive")
        if self.jitter_ratio < 0:
            raise ValueError("jitter_ratio must be non-negative")
        self._next_failure_delay_seconds = min(
            self.initial_failure_delay_seconds,
            self.refresh_interval_seconds,
        )

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=self.worker_name,
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=timeout)
        if not thread.is_alive():
            self._thread = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def _run_loop(self) -> None:
        delay_seconds = self._jittered(self.refresh_interval_seconds)
        while not self._stop.wait(delay_seconds):
            delay_seconds = self._run_refresh_once()

    def _run_refresh_once(self) -> float:
        if not self.status_provider.platform_registration_ready():
            return self._jittered(self.refresh_interval_seconds)
        try:
            self.registration_refresh()
        except Exception as exc:
            self.status_provider.mark_platform_registration_refresh_failed(str(exc))
            logger.warning("validator platform registration refresh failed", exc_info=exc)
            delay_seconds = self._jittered(self._next_failure_delay_seconds)
            self._next_failure_delay_seconds = min(
                self._next_failure_delay_seconds * 2,
                self.refresh_interval_seconds,
            )
            return delay_seconds
        self.status_provider.mark_platform_registration_succeeded()
        self._next_failure_delay_seconds = min(
            self.initial_failure_delay_seconds,
            self.refresh_interval_seconds,
        )
        return self._jittered(self.refresh_interval_seconds)

    def _jittered(self, delay_seconds: float) -> float:
        if self.jitter_ratio == 0:
            return delay_seconds
        multiplier = 1 - self.jitter_ratio + (self.random_value() * self.jitter_ratio * 2)
        return max(0.0, delay_seconds * multiplier)


def create_registration_refresh_worker(
    *,
    registration_refresh: Callable[[], None],
    status_provider: StatusProvider,
    refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
) -> RegistrationRefreshWorker:
    return RegistrationRefreshWorker(
        registration_refresh=registration_refresh,
        status_provider=status_provider,
        refresh_interval_seconds=refresh_interval_seconds,
    )


__all__ = [
    "DEFAULT_INITIAL_FAILURE_DELAY_SECONDS",
    "DEFAULT_JITTER_RATIO",
    "DEFAULT_REFRESH_INTERVAL_SECONDS",
    "RegistrationRefreshWorker",
    "create_registration_refresh_worker",
]
