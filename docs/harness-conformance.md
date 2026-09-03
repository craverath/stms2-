# Harness conformance

Real Codex, Claude Code, and experimental Pi checks are manual and opt-in. They
must run in a disposable Git repository with no production credentials in the
working tree. They are not part of the deterministic test suite.

For each installed adapter, verify preflight detects the executable/SDK version,
authentication, selected model and effort, absolute worktree cwd, and required
tool policy. Start a session, stream normalized events, cancel it, and resume it
after restarting STMS. Confirm timeout behavior and a valid typed result. Verify
planner/reviewer write restrictions, implementer worktree-only access, and no
network for test commands by default. Record only normalized event metadata and
optional usage; never record provider payloads, credentials, prompts containing
secrets, or reasoning.

Pi is experimental because its JSONL RPC does not provide STMS's sandbox or JSON
Schema guarantees natively. Check fragmented JSONL, cancellation, process exit,
and structured-output repair. A third invalid typed result must pause rather than
be accepted as free text.
