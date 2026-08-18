---
name: preserve-debug-evidence
description: Record the identifiers and observations needed before changing workflow direction. Use before switching hypotheses, changing config, or starting a new iteration after platform diagnosis.
---

# Preserve Debug Evidence

## Goal

Keep enough evidence to continue diagnosis without relying on memory.

## Inputs

- submit response
- local hash
- MCP monitoring outputs
- local eval reports when available

## Steps

1. Record identifiers:
   - `artifact_id`
   - `content_hash`
   - `batch_id`
   - `task_id` examples
   - signing hotkey
2. Record timing:
   - submit time
   - relevant `cutoff_at`
   - batch completion time when present
3. Record observed failures:
   - errors
   - timeouts
   - weak-answer examples
   - surprising judge rationale
4. Record the diagnosis already attempted.
5. Keep this evidence beside local eval reports or iteration notes.

## Stop Conditions

- Stop changing direction if the artifact, batch, or task identifiers are not
  recorded.

## Output

- compact evidence bundle for the next workflow action
