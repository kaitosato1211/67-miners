from __future__ import annotations

import pytest

from harnyx_validator.application.status import StatusProvider
from harnyx_validator.runtime.registration_worker import RegistrationRefreshWorker


def test_registration_refresh_worker_waits_until_initial_registration_ready() -> None:
    calls: list[str] = []
    worker = RegistrationRefreshWorker(
        registration_refresh=lambda: calls.append("refresh"),
        status_provider=StatusProvider(),
        refresh_interval_seconds=60.0,
        random_value=lambda: 0.5,
    )

    delay = worker._run_refresh_once()

    assert calls == []
    assert delay == 60.0


def test_registration_refresh_worker_records_success_and_resets_failure_backoff() -> None:
    calls: list[str] = []
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()
    worker = RegistrationRefreshWorker(
        registration_refresh=lambda: calls.append("refresh"),
        status_provider=status_provider,
        refresh_interval_seconds=60.0,
        random_value=lambda: 0.5,
    )

    delay = worker._run_refresh_once()

    assert calls == ["refresh"]
    assert delay == 60.0
    assert status_provider.platform_registration_ready()
    assert status_provider.state.platform_registration_last_succeeded_at is not None
    assert status_provider.state.platform_registration_last_refresh_error is None
    assert status_provider.state.platform_registration_refresh_failure_count == 0


def test_registration_refresh_worker_records_failure_without_clearing_ready() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()

    def _fail() -> None:
        raise RuntimeError("platform unavailable")

    worker = RegistrationRefreshWorker(
        registration_refresh=_fail,
        status_provider=status_provider,
        refresh_interval_seconds=60.0,
        random_value=lambda: 0.5,
    )

    delay = worker._run_refresh_once()

    assert delay == 5.0
    assert status_provider.platform_registration_ready()
    assert status_provider.platform_registration_error() is None
    assert status_provider.state.platform_registration_last_refresh_error == "platform unavailable"
    assert status_provider.state.platform_registration_refresh_failure_count == 1


def test_registration_refresh_worker_exponential_backoff_caps_at_refresh_interval() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()

    def _fail() -> None:
        raise RuntimeError("platform unavailable")

    worker = RegistrationRefreshWorker(
        registration_refresh=_fail,
        status_provider=status_provider,
        refresh_interval_seconds=60.0,
        random_value=lambda: 0.5,
    )

    delays = [worker._run_refresh_once() for _ in range(6)]

    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]


def test_registration_refresh_worker_jitters_delay() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()
    worker = RegistrationRefreshWorker(
        registration_refresh=lambda: None,
        status_provider=status_provider,
        refresh_interval_seconds=60.0,
        jitter_ratio=0.2,
        random_value=lambda: 1.0,
    )

    delay = worker._run_refresh_once()

    assert delay == pytest.approx(72.0)
