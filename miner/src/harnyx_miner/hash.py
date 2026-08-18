from __future__ import annotations

import argparse
from collections.abc import Sequence

from harnyx_miner.agent_source import agent_sha256, load_agent_bytes, require_existing_agent_path


def _hash_agent(*, agent_path: str) -> str:
    return agent_sha256(load_agent_bytes(require_existing_agent_path(agent_path)))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compute the SHA-256 digest for a miner agent script.")
    parser.add_argument("--agent-path", required=True, help="Path to the miner agent Python file.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        digest = _hash_agent(agent_path=args.agent_path)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        raise SystemExit(str(exc)) from exc

    print(digest)


__all__ = ["main", "_hash_agent"]
