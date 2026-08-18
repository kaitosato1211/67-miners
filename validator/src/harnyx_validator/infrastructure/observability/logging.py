"""Validator logging helpers built on shared commons logging."""

from __future__ import annotations

import os

from harnyx_commons.observability.logging import build_log_config as _build_log_config
from harnyx_commons.observability.logging import configure_logging as _configure_logging


def _validator_extra_loggers() -> dict[str, dict[str, str]]:
    return {
        "harnyx_validator.tools": {"level": "INFO"},
        "harnyx_validator.infrastructure.sandbox": {"level": "INFO"},
        "harnyx_validator.sandbox": {"level": "INFO"},
        "harnyx_validator.measurement": {
            "level": os.getenv("VALIDATOR_MEASUREMENT_LOG_LEVEL", "WARNING").upper()
        },
    }

__all__ = ["build_log_config", "configure_logging", "init_logging", "enable_cloud_logging"]


def init_logging() -> None:
    """Bootstrap console logging without cloud handlers."""

    _configure_logging(
        root_level_env="LOG_LEVEL",
        root_default="INFO",
        extra_loggers=_validator_extra_loggers(),
        cloud_logging_enabled=False,
        gcp_project=None,
        cloud_log_labels=None,
    )


def build_log_config(
    *,
    cloud_logging_enabled: bool = False,
    gcp_project: str | None = None,
    cloud_log_labels: dict[str, str] | None = None,
) -> dict[str, object]:
    return _build_log_config(
        root_level_env="LOG_LEVEL",
        root_default="INFO",
        extra_loggers=_validator_extra_loggers(),
        cloud_logging_enabled=cloud_logging_enabled,
        gcp_project=gcp_project,
        cloud_log_labels=cloud_log_labels,
    )


def configure_logging(
    *,
    cloud_logging_enabled: bool = False,
    gcp_project: str | None = None,
    cloud_log_labels: dict[str, str] | None = None,
) -> None:
    _configure_logging(
        root_level_env="LOG_LEVEL",
        root_default="INFO",
        extra_loggers=_validator_extra_loggers(),
        cloud_logging_enabled=cloud_logging_enabled,
        gcp_project=gcp_project,
        cloud_log_labels=cloud_log_labels,
    )


def enable_cloud_logging(
    *,
    gcp_project: str,
    cloud_log_labels: dict[str, str] | None = None,
) -> None:
    """Attach cloud logging on top of the existing console setup."""

    configure_logging(
        cloud_logging_enabled=True,
        gcp_project=gcp_project,
        cloud_log_labels=cloud_log_labels,
    )
