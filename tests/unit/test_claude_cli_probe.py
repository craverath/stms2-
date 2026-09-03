import subprocess

import pytest

from stms.adapters.harnesses.claude import ClaudeAgentSdkTransport


def _runner(responses):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs)); value = responses.pop(0)
        if isinstance(value, BaseException): raise value
        return value
    return calls, run


def test_claude_probe_uses_safe_cli_and_accepts_strict_json(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.shutil.which", lambda _: "/usr/bin/claude")
    calls, runner = _runner([
        subprocess.CompletedProcess([], 0, '{"authenticated":true}', ""),
        subprocess.CompletedProcess([], 0, '{"type":"result"}', ""),
    ])
    assert ClaudeAgentSdkTransport(command_runner=runner).preflight(model="claude-model", effort="high") == {"authenticated": True, "model": True, "effort": True}
    assert calls[0][0] == ["claude", "auth", "status", "--json"]
    assert calls[1][0] == ["claude", "--print", "--output-format", "json", "--model", "claude-model", "--effort", "high", "--max-turns", "1", "--restricted", "--safe-mode", "--no-session-persistence"]
    assert calls[1][1]["input"] == "Respond with {} only."


@pytest.mark.parametrize("auth,probe,expected", [
    (subprocess.CompletedProcess([], 1, "", ""), None, {"authenticated": False, "model": False, "effort": False}),
    (subprocess.CompletedProcess([], 0, '{"authenticated":true}', ""), subprocess.CompletedProcess([], 1, "", "invalid model"), {"authenticated": True, "model": False, "effort": False}),
    (subprocess.CompletedProcess([], 0, '{"authenticated":true}', ""), subprocess.CompletedProcess([], 0, "not-json", ""), {"authenticated": True, "model": False, "effort": False}),
])
def test_claude_probe_rejects_auth_or_model_effort_failures(monkeypatch, auth, probe, expected) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.shutil.which", lambda _: "/usr/bin/claude")
    values = [auth] if probe is None else [auth, probe]
    _, runner = _runner(values)
    assert ClaudeAgentSdkTransport(command_runner=runner).preflight(model="m", effort="bad") == expected


def test_claude_probe_timeout_is_actionable_failure(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.shutil.which", lambda _: "/usr/bin/claude")
    _, runner = _runner([subprocess.TimeoutExpired(["claude"], 10)])
    assert ClaudeAgentSdkTransport(command_runner=runner).preflight(model="m", effort="high") == {"authenticated": False, "model": False, "effort": False}
