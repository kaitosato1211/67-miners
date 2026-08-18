# harnyx-commons

Shared utilities and transparent incentive-mechanism logic for the Harnyx Subnet workspace.

This package provides:

- **Miner incentive rules** — miner-task dataset/reference generation, validator scoring, failure attribution, ranking aggregation, champion selection, benchmark scoring, component emission composition, participant emission math, and capped champion-emission math
- **Sandbox runner** — Docker container lifecycle management for validators (start/stop sandbox containers, HTTP client for calling sandbox entrypoints)
- **Tool infrastructure** — LLM and search tool adapters, cost tracking, budget validation
- **Observability** — logging and tracing utilities
- **Domain types** — shared data structures (queries, responses, sessions, scores, etc.)

Platform services, validator runtime orchestration, storage, auth, deployment, and other infrastructure remain outside this package. The miner-task modules here are the public source of truth for the business rules that affect scoring, champion selection, and emissions.
