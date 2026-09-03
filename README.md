# STMS

STMS is a local, deterministic workflow for agent-assisted development on macOS
and Linux with Python 3.12+. It keeps planning, implementation, testing, review,
and the final human merge gate auditable without sending telemetry.

## Status

STMS 0.1.0 is ready for local evaluation from this Git repository. This project
is not published on PyPI; the package named `stms` there is unrelated. Install
from the Git URL below. Real-provider conformance checks are still manual.

## Quick start

You need macOS or Linux, Python 3.12+, Git, [uv](https://docs.astral.sh/uv/),
Node.js 20.11+, and `ripgrep`. Install the Codex CLI, the sandbox runtime, and
STMS:

```shell
npm install -g @openai/codex @anthropic-ai/sandbox-runtime
codex login
codex login status
srt --version
uv tool install "git+https://github.com/craverath/stms2-.git"
stms --help
```

STMS runs inside the project it will modify. That project must be a Git
repository with at least one commit, an attached branch, clean tracked files,
and a configured author:

```shell
cd /path/to/your-project
git config user.name "Your Name"
git config user.email "you@example.com"
git status
```

Create `stms.yml` at the repository root from the packaged example:

```shell
curl -fsSL \
  https://raw.githubusercontent.com/craverath/stms2-/main/src/stms/stms.example.yml \
  -o stms.yml
```

Edit `stms.yml` before the first run. For the simplest setup, select `codex` for
all three roles and replace every `example-model` with a model ID available in
your Codex installation. Keep an effort supported by that model. The complete
schema and safe defaults are in [`stms.example.yml`](src/stms/stms.example.yml).

Then start a run from the repository root:

```shell
stms start "Add a login screen"
stms start --file PRD.md
stms resume
stms resume <run-id>
```

To use Claude Code for one or more roles, install STMS with its optional SDK and
install/authenticate Claude Code before selecting `harness: claude`:

```shell
uv tool install "stms[claude] @ git+https://github.com/craverath/stms2-.git"
npm install -g @anthropic-ai/claude-code
claude
claude auth status
```

Codex and Claude are supported; Pi is experimental. STMS deliberately does not
create `stms.yml`, install project dependencies, authenticate accounts, push,
deploy, or weaken sandbox policy. A missing harness, unavailable model/effort,
or incompatible sandbox stops preflight with a corrective action.

## Workflow

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
