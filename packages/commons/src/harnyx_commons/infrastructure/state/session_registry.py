"""In-memory session registry implementation."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from uuid import UUID

from harnyx_commons.application.ports.session_registry import SessionRegistryPort
from harnyx_commons.domain.session import Session


class InMemorySessionRegistry(SessionRegistryPort):
    """Stores session snapshots in memory."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._lock = Lock()

    def create(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: UUID) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
        return session

    def update(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def mutate(self, session_id: UUID, mutate: Callable[[Session], Session]) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise LookupError(f"session {session_id} not found")
            updated = mutate(session)
            self._sessions[session_id] = updated
            return updated

    def delete(self, session_id: UUID) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


__all__ = ["InMemorySessionRegistry"]
