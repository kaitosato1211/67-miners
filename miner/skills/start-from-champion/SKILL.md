---
name: start-from-champion
description: Seed a miner iteration from current champion context exposed through public MCP. Use when starting a candidate from scratch or comparing local behavior against champion context.
---

# Start From Champion

## Goal

Use public MCP champion context to find a baseline, then decide whether to seed
`agent.py` from revealed script content or fall back to local eval context.

## Inputs

- MCP tools: `get_champion`, `get_benchmark`, `get_miner_script`
- local candidate path, usually `./agent.py`

## Steps

1. Call `get_champion`.
2. If `champion.script_id` is present, call `get_miner_script` with:
   - `artifact_id=<champion.script_id>`
   - `include_content=true`
3. If source-batch context is needed, call `get_benchmark` and inspect:
   - `current_champion.script_id`
   - `latest_source_batch.champion_artifact_id`
4. Decode `content_b64` only when script content is revealed.
5. Treat revealed code as a baseline to inspect, not as finished work.
6. If script content is unavailable, run or inspect local eval against the latest
   completed batch instead.

## Stop Conditions

- Stop script-fetch attempts if the artifact is not visible yet.
- Do not assume batch-running artifacts expose script content.

## Output

- champion identifier fields found
- script content status
- baseline notes or local-eval fallback
