# STMS

STMS is a local, deterministic workflow for agent-assisted development on macOS
and Linux with Python 3.12+. It keeps planning, implementation, testing, review,
and the final human merge gate auditable without sending telemetry.

## Install

Install [uv](https://docs.astral.sh/uv/) first, then install from a Git checkout:

```shell
uv tool install .
stms --help
```

STMS needs a Git repository with a valid `HEAD`, an attached branch, clean
tracked changes, and configured `user.name` and `user.email`. Untracked files
are allowed but are never copied into agent worktrees unless the approved plan
explicitly lists them.

Install and authenticate the Codex and/or Claude Code harnesses independently.
STMS does not install packages, authenticate accounts, push, deploy, or weaken
sandbox policy. Install the Anthropic Sandbox Runtime (`srt`) and check that
`srt --version` works, unless an equivalent native fallback is explicitly
allowed in project configuration.

## Configure

Create `stms.yml` manually at the project root; STMS deliberately never creates
it. Start with [`stms.example.yml`](stms.example.yml), set real model IDs, and
select installed harnesses. Codex and Claude are supported; Pi is experimental.
Model/effort and sandbox capability mismatches stop preflight with a corrective
action rather than silently falling back.

## Use

```shell
stms start "Add a login screen"
stms start --file PRD.md
stms resume
stms resume <run-id>
```

`start` accepts exactly one prompt or `--file`. Preflight runs before a run,
lock, branch, or worktree is created. The planner asks at most three related
questions per turn. It writes `plan.md` and `context.md`, then waits for explicit
human approval. Feedback returns to planning; abort preserves the audit trail.
After approval, the plan's commands, dependencies, allowed untracked files, and
permissions are frozen.

Independent tasks run in isolated worktrees, are tested deterministically, and
are integrated serially into an integration branch. The original branch remains
unchanged until the final gate. STMS runs focused tests and the full approved
suite based only on process results, not agent claims. Reviews use fresh,
read-only sessions and follow four conditional severity thresholds. A high
finding in round four pauses for a human.

At the final gate, choose approve, adjust, replan, or abort. Approval compares
the current base with the recorded base and creates one squash commit only when
it is unchanged. STMS never rebase-merges a changed base automatically.

## State, interruption, and troubleshooting

Each run lives in `.stms/estado/<run-id>/` and contains `checkpoint.sqlite`,
`state.json`, `plan.md`, `context.md`, `events.jsonl`, test logs, and review
results. SQLite is the operational source of truth; the other files are readable
projections. A safe Ctrl-C pauses at a checkpoint and exits with code 3; use
`resume` to continue. Exit codes are 0 (completed), 1 (terminal failure), 2
(invalid input/configuration), 3 (paused), and 130 (forced interrupt).

Common preflight fixes: run from the repository root, make tracked changes
clean, create `stms.yml`, configure Git identity, finish an existing run, and
install/configure the selected harness and sandbox. Provider sessions are
auxiliary: a lost session is rehydrated from persisted state.

## Limits and conformance

Windows, remote workers, pull requests, pushes, CI/CD, deployment, external
telemetry, and automatic dependency installation are out of scope. The automated
suite uses fake harnesses only and requires neither network access nor provider
credentials. See [architecture](docs/architecture.md), [adapter contracts](docs/adapters.md),
and [manual harness conformance](docs/harness-conformance.md) for extension and
real-provider validation guidance.
