from __future__ import annotations

import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_AGENT_MODULE = "commons.tests.integration.sandbox.seccomp_agent"


def test_sandbox_start_failure_captures_container_stderr(
    sandbox_launcher,
    tmp_path: Path,
) -> None:
    diagnostics_dir = tmp_path / "sandbox-diagnostics"

    with pytest.raises(RuntimeError, match="sandbox healthz did not succeed"):
        sandbox_launcher(
            agent_module=_AGENT_MODULE,
            command=("--definitely-invalid",),
            failure_diagnostics_dir=diagnostics_dir,
            healthz_timeout=3.0,
        )

    docker_logs_path = diagnostics_dir / "docker-logs.txt"
    assert "unrecognized arguments: --definitely-invalid" in docker_logs_path.read_text(
        encoding="utf-8",
    )
    assert stat.S_IMODE(docker_logs_path.stat().st_mode) == 0o600
