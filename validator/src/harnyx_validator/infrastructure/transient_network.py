"""Transient network exception classification for validator retry owners."""

from __future__ import annotations

import errno
import re
import socket
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

_CONNECTION_INTERRUPTED_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNRESET,
    errno.ETIMEDOUT,
}
_WEBSOCKET_HANDSHAKE_TIMEOUT_MESSAGES = frozenset(
    {
        "timed out while waiting for handshake response",
    }
)
_SSL_HANDSHAKE_TIMEOUT_MESSAGE = re.compile(
    r"^_ssl\.c:\d+: The handshake operation timed out$"
)


@dataclass(frozen=True, slots=True)
class TransientNetworkCause:
    """Sanitized transient network cause metadata."""

    kind: str
    exception_type: str
    errno: int | None = None


def classify_transient_network_failure(exc: BaseException) -> TransientNetworkCause | None:
    """Return sanitized cause metadata when an exception is clearly transient network failure."""

    for candidate in _exception_chain(exc):
        if _is_websocket_handshake_timeout(candidate):
            return TransientNetworkCause(
                kind="websocket_handshake_timeout",
                exception_type=type(candidate).__name__,
            )
        if isinstance(candidate, socket.gaierror) and candidate.errno == socket.EAI_AGAIN:
            return TransientNetworkCause(
                kind="temporary_dns",
                exception_type=type(candidate).__name__,
                errno=socket.EAI_AGAIN,
            )
        if isinstance(candidate, OSError):
            candidate_errno = candidate.errno
            if candidate_errno == socket.EAI_AGAIN:
                return TransientNetworkCause(
                    kind="temporary_dns",
                    exception_type=type(candidate).__name__,
                    errno=socket.EAI_AGAIN,
                )
            if candidate_errno in _CONNECTION_INTERRUPTED_ERRNOS:
                return TransientNetworkCause(
                    kind="connection_interrupted",
                    exception_type=type(candidate).__name__,
                    errno=candidate_errno,
                )
        if isinstance(candidate, httpx.ConnectTimeout):
            return TransientNetworkCause(
                kind="connect_timeout",
                exception_type=type(candidate).__name__,
            )
    return None


def _is_websocket_handshake_timeout(exc: BaseException) -> bool:
    if type(exc) is not TimeoutError:
        return False
    message = str(exc)
    return (
        message in _WEBSOCKET_HANDSHAKE_TIMEOUT_MESSAGES
        or _SSL_HANDSHAKE_TIMEOUT_MESSAGE.fullmatch(message) is not None
    )


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    pending = [exc]
    while pending:
        candidate = pending.pop()
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        yield candidate

        for nested in candidate.args:
            if isinstance(nested, BaseException):
                pending.append(nested)
        if candidate.__cause__ is not None:
            pending.append(candidate.__cause__)
        if candidate.__context__ is not None:
            pending.append(candidate.__context__)


__all__ = ["TransientNetworkCause", "classify_transient_network_failure"]
