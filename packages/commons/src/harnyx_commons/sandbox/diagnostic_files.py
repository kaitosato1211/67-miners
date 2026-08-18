"""Owner-only file writes for sandbox diagnostic artifacts."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

PRIVATE_DIAGNOSTIC_DIR_MODE = 0o700
PRIVATE_DIAGNOSTIC_FILE_MODE = 0o600


def ensure_private_diagnostic_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIAGNOSTIC_DIR_MODE)
    path.chmod(PRIVATE_DIAGNOSTIC_DIR_MODE)


def write_private_text(path: Path, content: str) -> None:
    write_private_bytes(path, content.encode("utf-8"))


def write_private_bytes(path: Path, content: bytes) -> None:
    fd = os.open(
        path,
        os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
        PRIVATE_DIAGNOSTIC_FILE_MODE,
    )
    try:
        os.fchmod(fd, PRIVATE_DIAGNOSTIC_FILE_MODE)
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise RuntimeError(f"failed to write private diagnostic file: {path}")
            view = view[written:]
    finally:
        os.close(fd)


def write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
