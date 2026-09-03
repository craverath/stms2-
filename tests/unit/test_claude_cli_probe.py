from stms.adapters.harnesses.claude import ClaudeAgentSdkTransport


def test_claude_preflight_requires_installation(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.importlib.util.find_spec", lambda _name: None)
    monkeypatch.setattr(ClaudeAgentSdkTransport, "_executable_available", lambda self: False)
    assert ClaudeAgentSdkTransport().preflight(model="m", effort="high") == {"authenticated": False, "model": False, "effort": False}


def test_claude_preflight_fails_closed_when_installed_but_unauthenticated(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.importlib.util.find_spec", lambda _name: object())
    assert ClaudeAgentSdkTransport(auth_probe=lambda: False).preflight(model="m", effort="high") == {"authenticated": False, "model": False, "effort": False}


def test_claude_preflight_accepts_documented_oauth_status_probe(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.importlib.util.find_spec", lambda _name: object())
    assert ClaudeAgentSdkTransport(auth_probe=lambda: True).preflight(model="claude-model", effort="high") == {"authenticated": True, "model": True, "effort": True}


def test_claude_preflight_rejects_empty_model_or_effort(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.claude.importlib.util.find_spec", lambda _name: object())
    assert ClaudeAgentSdkTransport(auth_probe=lambda: True).preflight(model="", effort="high") == {"authenticated": True, "model": False, "effort": False}
