# Miner Workflow Skills

These public skills break the miner workflow into small steps that a human or
code agent can read from the repo root.

Harnesses can load [`manifest.json`](manifest.json) to discover the available
skills, then read each `SKILL.md` when its workflow is needed.

## Local Improvement Loop

1. `prepare-benchmark-context`
2. `improve-artifact`
3. `run-local-eval`

Repeat until local reports justify submitting.

## Platform Workflow

1. `connect-platform-mcp`
2. `start-from-champion`
3. `configure-before-submit`
4. `submit-and-confirm`
5. `monitor-batch`
6. `inspect-completed-results`
7. `diagnose-score`
8. `preserve-debug-evidence`

Use the platform workflow after local eval shows a candidate worth submitting,
or when platform state explains a confusing score or batch result.
