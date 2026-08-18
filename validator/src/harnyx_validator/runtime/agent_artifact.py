"""Agent artifact resolution and validation utilities (platform-provided only)."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx

from harnyx_commons.sandbox.agent_staging import (
    MAX_AGENT_BYTES,
    AgentArtifact,
    AgentSourceValidationError,
    stage_agent_source,
)
from harnyx_validator.application.dto.evaluation import ScriptArtifactSpec
from harnyx_validator.application.ports.platform import PlatformPort
from harnyx_validator.infrastructure.tools.platform_client import PlatformClientError

logger = logging.getLogger("harnyx_validator.agent_artifact")

_FETCH_RETRY_ATTEMPTS = 3
_FETCH_INITIAL_BACKOFF_SECONDS = 0.25


class ArtifactPreparationError(RuntimeError):
    """Raised when a single artifact cannot be fetched, validated, or staged."""

    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.exception_type = exception_type


def resolve_platform_agent_spec(
    *,
    batch_id: UUID,
    artifact: ScriptArtifactSpec,
    platform_client: PlatformPort,
    state_dir: Path,
    container_root: str,
) -> AgentArtifact:
    """Resolve one platform-provided agent artifact."""

    data = _fetch_platform_artifact(
        batch_id=batch_id,
        artifact=artifact,
        platform_client=platform_client,
    )
    content_hash = hashlib.sha256(data).hexdigest()
    if content_hash != artifact.content_hash:
        logger.error(
            "Platform agent sha256 mismatch",
            extra={
                "batch_id": str(batch_id),
                "uid": artifact.uid,
                "artifact_id": str(artifact.artifact_id),
                "expected_sha256": artifact.content_hash,
                "actual_sha256": content_hash,
            },
        )
        raise ArtifactPreparationError(
            error_code="artifact_hash_mismatch",
            message=(
                "platform agent sha256 mismatch "
                f"(expected={artifact.content_hash} actual={content_hash})"
            ),
        )

    try:
        resolved = _stage_platform_agent(
            content_hash=content_hash,
            data=data,
            state_dir=state_dir,
            container_root=container_root,
        )
    except AgentSourceValidationError as exc:
        logger.error(
            "Platform agent failed script validation during staging",
            extra={"batch_id": str(batch_id), "uid": artifact.uid, "artifact_id": str(artifact.artifact_id)},
            exc_info=exc,
        )
        raise ArtifactPreparationError(
            error_code="script_validation_failed",
            message=str(exc),
            exception_type=type(exc).__name__,
        ) from exc
    except Exception as exc:
        logger.error(
            "Failed to stage platform agent",
            extra={"batch_id": str(batch_id), "uid": artifact.uid, "artifact_id": str(artifact.artifact_id)},
            exc_info=exc,
        )
        raise ArtifactPreparationError(
            error_code="artifact_staging_failed",
            message=str(exc),
            exception_type=type(exc).__name__,
        ) from exc

    logger.info(
        "Staged platform agent",
        extra={
            "uid": artifact.uid,
            "artifact_id": str(artifact.artifact_id),
            "content_hash": content_hash,
            "host_path": str(resolved.host_path),
            "container_path": resolved.container_path,
        },
    )
    return resolved


def _fetch_platform_artifact(
    *,
    batch_id: UUID,
    artifact: ScriptArtifactSpec,
    platform_client: PlatformPort,
) -> bytes:
    last_error: Exception | None = None
    for attempt_number in range(1, _FETCH_RETRY_ATTEMPTS + 1):
        try:
            return platform_client.fetch_artifact(batch_id, artifact.artifact_id)
        except Exception as exc:
            last_error = exc
            if _is_retryable_fetch_error(exc) and attempt_number < _FETCH_RETRY_ATTEMPTS:
                backoff_seconds = _fetch_backoff_seconds(attempt_number)
                logger.warning(
                    "Transient platform artifact fetch failed; retrying",
                    extra={
                        "batch_id": str(batch_id),
                        "uid": artifact.uid,
                        "artifact_id": str(artifact.artifact_id),
                        "attempt_number": attempt_number,
                        "backoff_seconds": round(backoff_seconds, 2),
                    },
                    exc_info=exc,
                )
                time.sleep(backoff_seconds)
                continue
            logger.error(
                "Failed to fetch platform agent",
                extra={"batch_id": str(batch_id), "uid": artifact.uid, "artifact_id": str(artifact.artifact_id)},
                exc_info=exc,
            )
            raise ArtifactPreparationError(
                error_code="artifact_fetch_failed",
                message=str(exc),
                exception_type=type(exc).__name__,
            ) from exc

    if last_error is None:
        raise RuntimeError("artifact fetch retry loop exited without result")
    raise last_error


def _is_retryable_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, PlatformClientError):
        if exc.status_code is None:
            return False
        return 500 <= exc.status_code < 600
    return isinstance(exc, httpx.HTTPError)


def _fetch_backoff_seconds(attempt_number: int) -> float:
    return _FETCH_INITIAL_BACKOFF_SECONDS * (2 ** (attempt_number - 1))


def _stage_platform_agent(
    *,
    content_hash: str,
    data: bytes,
    state_dir: Path,
    container_root: str,
) -> AgentArtifact:
    artifact = stage_agent_source(
        state_dir=state_dir,
        container_root=container_root,
        namespace="platform_agents",
        key=content_hash,
        data=data,
        max_bytes=MAX_AGENT_BYTES,
    )
    if artifact.content_hash != content_hash:
        raise RuntimeError(
            "platform agent sha256 mismatch after staging "
            f"(expected={content_hash} actual={artifact.content_hash})"
        )
    return artifact


def create_platform_agent_resolver(
    platform_client: PlatformPort,
) -> Callable[[UUID, ScriptArtifactSpec, Path, str], AgentArtifact]:
    """Create a resolver function for one platform agent artifact."""

    def resolver(
        batch_id: UUID,
        artifact: ScriptArtifactSpec,
        state_dir: Path,
        container_root: str,
    ) -> AgentArtifact:
        return resolve_platform_agent_spec(
            batch_id=batch_id,
            artifact=artifact,
            platform_client=platform_client,
            state_dir=state_dir,
            container_root=container_root,
        )

    return resolver


__all__ = [
    "AgentArtifact",
    "ArtifactPreparationError",
    "MAX_AGENT_BYTES",
    "create_platform_agent_resolver",
    "resolve_platform_agent_spec",
]
