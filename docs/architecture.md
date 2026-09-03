# Architecture

STMS uses a dependency-inverted layout. `stms.domain` contains Pydantic
contracts, state transitions and policies without imports from Git, subprocess,
SQLite, LangGraph, terminal UI or vendor SDKs. `stms.application` coordinates
those contracts. `stms.deterministic` owns process/test/Git effects, and
`stms.adapters` translates persistence and sandbox infrastructure.

SQLite is the operational source of truth. Human-readable JSON, Markdown, JSONL
and logs are projections. Every external operation is checkpointed before and
after execution under an idempotent operation identifier.

`application.preflight` is read-only: it validates Git, configuration, selected
harness capabilities, sandbox, identity, and exclusivity before creating a run.
`application.orchestrator` owns approval gates, task waves, deterministic test
results, review policy, resume, and final merge. Agents only return typed semantic
output; `GitWorktreeManager` and `DeterministicTestRunner` retain authority over
Git and process outcomes. The terminal boundary serializes rendering and strips
control characters from agent-originated text.
