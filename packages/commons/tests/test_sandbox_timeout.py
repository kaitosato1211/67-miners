from __future__ import annotations

import pytest

import harnyx_commons.sandbox.timeout as timeout_module


def test_default_entrypoint_timeout_is_five_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(timeout_module.ENTRYPOINT_TIMEOUT_ENV_VAR, raising=False)

    assert timeout_module.load_entrypoint_timeout_seconds() == 300.0


def test_entrypoint_timeout_override_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(timeout_module.ENTRYPOINT_TIMEOUT_ENV_VAR, "5")

    assert timeout_module.load_entrypoint_timeout_seconds() == 5.0


def test_entrypoint_timeout_invalid_values_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(timeout_module.ENTRYPOINT_TIMEOUT_ENV_VAR, "0")

    with pytest.raises(ValueError, match="must be > 0"):
        timeout_module.load_entrypoint_timeout_seconds()
