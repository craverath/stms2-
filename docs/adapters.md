# Adapter contracts

Concrete implementations conform to the protocols in `stms.domain.ports`.
Agent harnesses, sandbox runtimes, artifact stores, checkpoint stores and event
sinks can therefore be replaced without changing domain policy. Provider payloads
and reasoning are not part of the contracts or persisted event format.

## Agent harnesses

`AgentHarness` starts, resumes and cancels sessions, sends follow-up turns, streams
normalized events, reports optional usage, and exposes capabilities for preflight.
All requests carry an absolute worktree `cwd`, model, effort, tool policy, timeout
and turn limit. Provider payloads are converted to the small STMS result and event
schemas before leaving an adapter.

Codex and Claude Code are supported adapters. Codex uses the official local
`codex app-server --stdio` JSON-RPC interface (the first-class interface behind
the TypeScript SDK), not an assumed Python package. Claude loads the optional
`claude-agent-sdk` package (extra `stms[claude]`) lazily, and only inside the
adapter's own client construction; importing STMS or selecting a different
harness never requires it. Both boundaries are injectable, so automated tests
never authenticate, call a model, or contact a provider. A missing runtime or a
SDK that cannot honour `cwd`, effort, tools, timeout or cancellation fails with
an actionable compatibility error; it never downgrades.
Codex preflight invokes `codex login status` and then lets App Server reject an
unavailable catalog model or effort without requesting provider fallback. Claude
has no documented local catalog/auth-status subcommand, so its preflight never
guesses at undocumented CLI flags: it checks that the SDK package or CLI
executable is present and that the single stable, documented credential
(`ANTHROPIC_API_KEY`) is set, and otherwise fails closed. Pi has no standard
probe either and fails closed unconditionally. Package presence alone never
passes that gate.

The Claude adapter calls the real, documented `claude_agent_sdk` API:
`ClaudeSDKClient` (not the one-shot `query` helper, because only the client
exposes `interrupt()` for cancellation and `receive_messages()` for streaming
after the first turn) constructed from `ClaudeAgentOptions` with `cwd`, `model`,
`effort`, `max_turns`, `permission_mode`, `allowed_tools`/`disallowed_tools`,
`resume`, and `output_format`. Its `sandbox` option is `SandboxSettings`
(`enabled` plus a `network.allowedDomains` allowlist derived from the prepared
STMS policy); per the SDK's own documentation, filesystem read/write
restriction is expressed through the tool allow/deny lists above, not through
`sandbox`. A run's final `ResultMessage` (matched by `isinstance`, not a class
name, so any offline test double must actually be an instance the adapter can
recognize) supplies the session ID, structured/text output and usage.
SDK calls derive their sandbox mode, network
allowlist and Git denial from the prepared STMS policy; a missing, malformed or
role-mismatched policy is rejected before a session starts.

Pi is experimental. It runs as a local `pi --mode rpc` JSONL subprocess and has no
claimed native sandbox or JSON-schema output capability. STMS composes it with the
configured `SandboxRuntime`, correlates JSONL message IDs, validates output, and
can ask for at most two structured-output repairs. It terminates the Pi child after
cancellation or an interrupted request. Invalid JSONL, process crashes, and a third
invalid output pause the workflow rather than becoming free text. Pi has no
standard RPC authentication/model probe, so production preflight fails closed until
a compatible local Pi integration provides one; this is intentional, not a fallback.

## Manual conformance

Real-provider checks are intentionally outside the automated suite. In a disposable
repository, verify for each installed adapter: authentication and version in
preflight; selected model and effort; absolute worktree cwd and least-privilege
tools; start/resume after process restart; normalized streaming; cancellation;
timeout; typed output; and sandbox behavior. Record only normalized events and
usage—never raw provider payloads, prompts containing secrets, or reasoning.

The SRT adapter fails closed when its executable or required capabilities are
missing. It invokes the real SRT 1.0.0 CLI contract, `srt --settings <file>
<command...>`, translating STMS's internal allow-list `SandboxPolicy` into
SRT's own settings schema (`filesystem.{allowRead,denyRead,allowWrite,
denyWrite}`, `network.{allowedDomains,deniedDomains}`). Because SRT is beta,
`capabilities()` does not trust `--version` alone: it runs a real functional
probe — an actual wrapped subprocess attempting a denied and an allowed write,
and a denied and an allowed loopback connection — and only reports
`filesystem_policy`/`network_policy` as supported when that probe proves it;
anything it cannot prove is reported unsupported rather than assumed. A native
fallback must be explicitly enabled and prove it can satisfy the requested
least-privilege policy.
