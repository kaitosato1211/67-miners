---
name: submit-and-confirm
description: Submit a miner artifact and confirm platform acceptance. Use after local evaluation justifies uploading agent.py.
---

# Submit And Confirm

## Goal

Upload a miner script and verify that the platform accepted the exact local
content.

## Inputs

- agent path, usually `./agent.py`
- wallet name
- hotkey name
- `PLATFORM_BASE_URL=https://api.harnyx.ai`
- MCP tool: `get_latest_submissions`

## Steps

1. Set the platform base URL:

```bash
export PLATFORM_BASE_URL="https://api.harnyx.ai"
```

2. Hash the local script:

```bash
uv run --package harnyx-miner harnyx-miner-hash --agent-path ./agent.py
```

3. Upload the script:

```bash
uv run --package harnyx-miner harnyx-miner-submit \
  --agent-path ./agent.py \
  --wallet-name <wallet> \
  --hotkey-name <hotkey>
```

4. Compare the returned `content_hash` with the local hash.
5. Call `get_latest_submissions`; if the artifact has already moved, inspect finalized `initializing` or `running` batch detail.
6. Find the returned `artifact_id`, miner hotkey, and `content_hash` in one of those two disjoint views.
7. Record `artifact_id`, `content_hash`, signing hotkey, and submit time.

## Stop Conditions

- Stop if the returned hash does not match the local hash.
- Stop if neither current candidates nor finalized non-terminal batch membership shows the submitted artifact.

## Output

- `artifact_id`
- `content_hash`
- submit time
- signing wallet and hotkey
