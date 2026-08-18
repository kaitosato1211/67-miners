"""Shared entrypoint timeout contract for sandbox-style miner execution."""

from __future__ import annotations

import os

ENTRYPOINT_TIMEOUT_ENV_VAR = "ENTRYPOINT_TIMEOUT_SECONDS"


def load_entrypoint_timeout_seconds() -> float:
    raw_timeout = os.getenv(ENTRYPOINT_TIMEOUT_ENV_VAR, "300")
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise ValueError(
            f"{ENTRYPOINT_TIMEOUT_ENV_VAR} must be a number > 0, got {raw_timeout!r}",
        ) from exc
    if timeout_seconds <= 0:
        raise ValueError(
            f"{ENTRYPOINT_TIMEOUT_ENV_VAR} must be > 0, got {raw_timeout!r}",
        )
    return timeout_seconds


ENTRYPOINT_TIMEOUT_SECONDS = load_entrypoint_timeout_seconds()


__all__ = [
    "ENTRYPOINT_TIMEOUT_ENV_VAR",
    "ENTRYPOINT_TIMEOUT_SECONDS",
    "load_entrypoint_timeout_seconds",
]
