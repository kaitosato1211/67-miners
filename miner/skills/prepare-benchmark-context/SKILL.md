---
name: prepare-benchmark-context
description: Prepare local batch-eval context before editing a miner artifact.
---

# Prepare Benchmark Context

## Goal

Understand the current task set and the last local-eval report before changing
the artifact.

## Inputs

- target artifact path, usually `./agent.py`
- selected batch or latest completed batch
- newest local-eval JSON/Markdown report when available

## Steps

1. Confirm the target artifact path, usually `./agent.py`.
2. Confirm whether you are evaluating the latest completed batch or a specific `--batch-id`.
3. Open the newest `local-eval-report-<batch-id>-<mode>.json` if one already exists.
4. Identify:
   - lowest-scoring tasks
   - highest-cost tasks
   - repeated errors or retries
   - places where the champion beat the target in `vs-champion`
5. Write down 1-3 concrete hypotheses for what to improve.

## Stop Conditions

- Stop if the target artifact path is unclear.
- Stop if there is no report and no chosen batch context to inspect.

## Output

- chosen artifact path
- chosen batch / mode
- a short list of task-level problems
- a short list of improvement hypotheses
