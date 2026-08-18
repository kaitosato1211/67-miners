from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_public_env(*, cwd: Path | None = None) -> None:
    """Load public miner `.env` files without overriding process env values."""
    base = (cwd or Path.cwd()).resolve()
    candidates = (
        base / ".env",
        base.parent / ".env",
        Path(__file__).resolve().parents[3] / ".env",
    )
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        load_dotenv(dotenv_path=candidate, override=False)


__all__ = ["load_public_env"]
