from __future__ import annotations

import hashlib
from pathlib import Path

from harnyx_miner.hash import _hash_agent, main


def test_hash_agent_matches_sha256_for_agent_bytes(tmp_path: Path) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("from harnyx_miner_sdk.query import Response\n", encoding="utf-8")

    digest = _hash_agent(agent_path=str(agent_path))

    assert digest == hashlib.sha256(agent_path.read_bytes()).hexdigest()


def test_main_prints_hash(capsys, tmp_path: Path) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("print('ok')\n", encoding="utf-8")

    main(["--agent-path", str(agent_path)])

    assert capsys.readouterr().out.strip() == hashlib.sha256(agent_path.read_bytes()).hexdigest()
