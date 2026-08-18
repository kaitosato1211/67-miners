"""sr25519 signature verification for inbound control-plane RPC."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock, Thread

import bittensor as bt

from harnyx_commons.bittensor import VerificationError, verify_signed_request
from harnyx_validator.infrastructure.auth.header import parse_bittensor_header

logger = logging.getLogger("harnyx_validator.auth")

_FAILED_REFRESH_RETRY_SECONDS = 5.0


@dataclass(slots=True)
class BittensorSr25519InboundVerifier:
    """Validates sr25519-signed requests from the platform."""

    netuid: int
    network: str
    owner_coldkey_ss58: str
    refresh_interval_seconds: float = 300.0
    on_refresh_succeeded: Callable[[], None] | None = None
    on_refresh_failed: Callable[[str], None] | None = None
    _authorized_hotkeys: frozenset[str] | None = field(default=None, init=False, repr=False)
    _authorized_hotkeys_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _refresh_stop: Event = field(default_factory=Event, init=False, repr=False)
    _refresh_thread: Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        thread = self._refresh_thread
        if thread is not None and thread.is_alive():
            return
        self._refresh_stop.clear()
        thread = Thread(
            target=self._refresh_loop,
            name="validator-inbound-auth-refresh",
            daemon=True,
        )
        self._refresh_thread = thread
        thread.start()

    def stop(self, *, timeout_seconds: float) -> bool:
        thread = self._refresh_thread
        if thread is None:
            return True
        self._refresh_stop.set()
        thread.join(timeout=timeout_seconds)
        if thread.is_alive():
            logger.warning("timed out stopping inbound auth refresh thread")
            return False
        self._refresh_thread = None
        self._refresh_stop.clear()
        return True

    def refresh_authorization_state(self) -> None:
        authorized_hotkeys = self._fetch_authorized_hotkeys()
        with self._authorized_hotkeys_lock:
            self._authorized_hotkeys = authorized_hotkeys
        if self.on_refresh_succeeded is not None:
            self.on_refresh_succeeded()
        logger.info(
            "refreshed inbound auth hotkeys",
            extra={
                "data": {
                    "netuid": self.netuid,
                    "owner_coldkey_ss58": self.owner_coldkey_ss58,
                    "authorized_hotkey_count": len(authorized_hotkeys),
                }
            },
        )

    def _fetch_authorized_hotkeys(self) -> frozenset[str]:
        subtensor = bt.Subtensor(network=self.network)
        try:
            owned_hotkeys = subtensor.get_owned_hotkeys(self.owner_coldkey_ss58)
        finally:
            try:
                subtensor.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("subtensor close failed during inbound auth check")
        return frozenset(
            hotkey.strip()
            for hotkey in (str(raw_hotkey) for raw_hotkey in owned_hotkeys)
            if hotkey.strip()
        )

    def verify(
        self,
        *,
        method: str,
        path_qs: str,
        body: bytes,
        authorization_header: str | None,
    ) -> str:
        parsed = verify_signed_request(
            method=method,
            path_qs=path_qs,
            body=body,
            authorization_header=authorization_header,
            allowed_ss58=None,
            parse_header=parse_bittensor_header,
        )
        authorized_hotkeys = self._authorized_hotkeys_snapshot()
        if authorized_hotkeys is None:
            raise VerificationError(
                "auth_unavailable",
                "inbound auth verifier has not completed initial hotkey warmup",
            )
        if parsed.ss58 not in authorized_hotkeys:
            self._verify_owner_hotkey_on_chain(parsed.ss58)
            self._remember_authorized_hotkey(parsed.ss58)
        return parsed.ss58

    def _authorized_hotkeys_snapshot(self) -> frozenset[str] | None:
        with self._authorized_hotkeys_lock:
            return self._authorized_hotkeys

    def _verify_owner_hotkey_on_chain(self, hotkey_ss58: str) -> None:
        owner = self._fetch_hotkey_owner(hotkey_ss58)
        if owner is None:
            raise VerificationError("unknown_hotkey", "hotkey owner not found on chain")
        if owner != self.owner_coldkey_ss58:
            raise VerificationError("not_owner", "caller hotkey is not owned by subnet owner coldkey")

    def _fetch_hotkey_owner(self, hotkey_ss58: str) -> str | None:
        subtensor = bt.Subtensor(network=self.network)
        try:
            owner = subtensor.get_hotkey_owner(hotkey_ss58)
        finally:
            try:
                subtensor.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("subtensor close failed during inbound auth owner lookup")
        if owner is None:
            return None
        return str(owner)

    def _remember_authorized_hotkey(self, hotkey_ss58: str) -> None:
        with self._authorized_hotkeys_lock:
            authorized_hotkeys = self._authorized_hotkeys
            if authorized_hotkeys is None:
                self._authorized_hotkeys = frozenset((hotkey_ss58,))
                return
            self._authorized_hotkeys = authorized_hotkeys | {hotkey_ss58}

    def _refresh_loop(self) -> None:
        while True:
            wait_seconds = self.refresh_interval_seconds
            try:
                self.refresh_authorization_state()
            except Exception as exc:
                wait_seconds = min(self.refresh_interval_seconds, _FAILED_REFRESH_RETRY_SECONDS)
                if self.on_refresh_failed is not None:
                    self.on_refresh_failed(str(exc))
                logger.exception(
                    "failed to refresh inbound auth hotkeys",
                    extra={
                        "data": {
                            "netuid": self.netuid,
                            "owner_coldkey_ss58": self.owner_coldkey_ss58,
                        }
                    },
                )
            if self._refresh_stop.wait(wait_seconds):
                return


__all__ = ["BittensorSr25519InboundVerifier"]
