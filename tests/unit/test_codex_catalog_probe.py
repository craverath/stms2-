import subprocess

import pytest

from stms.adapters.harnesses.codex import CodexAppServerTransport


def _status(*_args, **_kwargs): return subprocess.CompletedProcess([], 0, "ok", "")


@pytest.mark.parametrize(("model", "effort", "expected"), [
    ("missing", "high", {"authenticated": True, "model": False, "effort": False}),
    ("codex", "xhigh", {"authenticated": True, "model": True, "effort": False}),
    ("alias", "high", {"authenticated": True, "model": True, "effort": True}),
])
def test_codex_preflight_uses_app_server_catalog(monkeypatch, model, effort, expected) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.codex.shutil.which", lambda _: "/usr/bin/codex")
    catalog = {"models": [{"id": "codex", "aliases": ["alias"], "supportedReasoningEfforts": [
        {"reasoningEffort": "low", "description": "Fast"},
        {"reasoningEffort": "high", "description": "Thorough"},
    ]}]}
    transport = CodexAppServerTransport(command_runner=_status, app_server_probe=lambda: catalog)
    assert transport.preflight(model=model, effort=effort) == expected


def test_codex_preflight_accepts_legacy_effort_strings(monkeypatch) -> None:
    monkeypatch.setattr("stms.adapters.harnesses.codex.shutil.which", lambda _: "/usr/bin/codex")
    catalog = {"models": [{"id": "codex", "supportedReasoningEfforts": ["low", "high"]}]}
    transport = CodexAppServerTransport(command_runner=_status, app_server_probe=lambda: catalog)
    assert transport.preflight(model="codex", effort="high")["effort"] is True
