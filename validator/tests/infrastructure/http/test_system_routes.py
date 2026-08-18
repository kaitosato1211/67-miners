from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harnyx_validator.application.status import StatusProvider
from harnyx_validator.infrastructure.http.middleware import request_logging_middleware
from harnyx_validator.infrastructure.http.routes import add_system_routes


class _ExplodingReadyStatusProvider:
    def platform_registration_ready(self) -> bool:
        raise RuntimeError("ready exploded")

    def auth_ready(self) -> bool:
        return False

    def auth_error(self) -> str | None:
        return None

    def platform_registration_error(self) -> str | None:
        return None


def _create_test_client(
    status_provider: StatusProvider,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    app = FastAPI()
    app.middleware("http")(request_logging_middleware)
    add_system_routes(app, status_provider)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_healthz_returns_ok() -> None:
    client = _create_test_client(StatusProvider())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_starting_until_platform_registration_completes() -> None:
    client = _create_test_client(StatusProvider())

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "waiting_for_platform_registration"}


def test_readyz_returns_ok_after_platform_registration() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()
    status_provider.mark_auth_ready()
    client = _create_test_client(status_provider)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_stays_ok_after_post_startup_registration_refresh_failure() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()
    status_provider.mark_auth_ready()
    status_provider.mark_platform_registration_refresh_failed("platform unavailable")
    client = _create_test_client(status_provider)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_waiting_for_auth_warmup_after_platform_registration() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()
    client = _create_test_client(status_provider)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "waiting_for_auth_warmup"}


def test_readyz_surfaces_auth_failures() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_succeeded()
    status_provider.mark_auth_unavailable("subtensor unavailable")
    client = _create_test_client(status_provider)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "auth_unavailable",
        "detail": "subtensor unavailable",
    }


def test_readyz_surfaces_registration_failures() -> None:
    status_provider = StatusProvider()
    status_provider.mark_platform_registration_failed("platform rejected validator")
    client = _create_test_client(status_provider)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "registration_failed",
        "detail": "platform rejected validator",
    }


def test_readyz_internal_error_uses_generic_500_body() -> None:
    client = _create_test_client(
        _ExplodingReadyStatusProvider(),  # type: ignore[arg-type]
        raise_server_exceptions=False,
    )

    response = client.get("/readyz", headers={"x-request-id": "req-456"})

    assert response.status_code == 500
    assert "ready exploded" not in response.text
