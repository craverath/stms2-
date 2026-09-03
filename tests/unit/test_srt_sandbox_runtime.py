"""Exercise SrtSandboxRuntime against a faithful fake `srt` CLI (fixtures/fake_srt.py).

The fixture implements the real SRT 1.0.0 contract (`srt --settings FILE cmd...`
with the filesystem allowRead/denyRead/allowWrite/denyWrite and network
allowedDomains/deniedDomains schema) and genuinely enforces it, so these tests
prove the adapter's translation and invocation, not just that it calls
`--version` successfully.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import pytest

from stms.adapters.sandbox.srt import SrtSandboxRuntime
from stms.domain.errors import InfrastructureError

FAKE_SRT = [sys.executable, str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_srt.py")]


def _fake_srt_executable(tmp_path: Path) -> str:
    """SrtSandboxRuntime always invokes a single `executable: str` as argv[0], so
    the two-part `python fake_srt.py` invocation is wrapped behind a tiny shell
    script to keep the adapter's public contract unchanged."""
    script = tmp_path / "srt"
    script.write_text(f"#!/bin/sh\nexec {FAKE_SRT[0]} {FAKE_SRT[1]} \"$@\"\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_capabilities_are_empirically_proven_supported_against_the_fake_cli(tmp_path: Path) -> None:
    runtime = SrtSandboxRuntime(executable=_fake_srt_executable(tmp_path), policy_directory=tmp_path / "policies")
    capabilities = {item.name: item.supported for item in runtime.capabilities()}
    assert capabilities["srt"] is True
    assert capabilities["filesystem_policy"] is True
    assert capabilities["network_policy"] is True
    assert capabilities["command_wrapping"] is True


def test_capability_probes_do_not_inherit_a_denied_home_cwd(tmp_path: Path, monkeypatch) -> None:
    runtime = SrtSandboxRuntime(executable=_fake_srt_executable(tmp_path), policy_directory=tmp_path / "policies")
    with tempfile.TemporaryDirectory(prefix=".stms-denied-cwd-", dir=Path.home()) as unrelated:
        monkeypatch.chdir(unrelated)
        capabilities = {item.name: item.supported for item in runtime.capabilities()}
    assert capabilities == {
        "srt": True,
        "filesystem_policy": True,
        "network_policy": True,
        "git_policy": True,
        "command_wrapping": True,
    }


def test_capabilities_mark_everything_unsupported_when_the_binary_is_absent(tmp_path: Path) -> None:
    runtime = SrtSandboxRuntime(executable=str(tmp_path / "does-not-exist"), policy_directory=tmp_path / "policies")
    capabilities = runtime.capabilities()
    assert len(capabilities) == 1
    assert capabilities[0].name == "srt" and capabilities[0].supported is False


def test_wrap_command_translates_policy_into_real_srt_settings_schema(tmp_path: Path) -> None:
    runtime = SrtSandboxRuntime(executable=_fake_srt_executable(tmp_path), policy_directory=tmp_path / "policies")
    worktree = tmp_path / "worktree"; worktree.mkdir()
    policy_path = runtime.prepare("implementer", tmp_path, worktree)

    wrapped = runtime.wrap_command(policy_path, ["true"])

    assert wrapped[0] == runtime.executable and wrapped[1] == "--settings"
    settings = json.loads(Path(wrapped[2]).read_text(encoding="utf-8"))
    assert settings["filesystem"]["allowWrite"] == [str(worktree.resolve())]
    assert settings["filesystem"]["allowRead"] == [str(worktree.resolve())]
    assert settings["filesystem"]["denyRead"] == [str(Path.home().resolve())]
    assert "allowedDomains" in settings["network"] and "deniedDomains" in settings["network"]
    assert wrapped[3] == "--"
    assert wrapped[4:] == ["true"]


def test_fake_srt_rejects_a_command_without_separator(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"filesystem": {}, "network": {}}')
    result = __import__("subprocess").run(
        [_fake_srt_executable(tmp_path), "--settings", str(settings), sys.executable, "-c", "pass"],
        capture_output=True,
    )
    assert result.returncode == 2


def test_wrap_command_actually_blocks_writes_outside_the_role_policy(tmp_path: Path) -> None:
    runtime = SrtSandboxRuntime(executable=_fake_srt_executable(tmp_path), policy_directory=tmp_path / "policies")
    worktree = tmp_path / "worktree"; worktree.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    policy_path = runtime.prepare("reviewer", tmp_path, worktree)  # reviewer: read-only

    script = f"import pathlib\npathlib.Path({str(worktree / 'x.txt')!r}).write_text('x')\n"
    argv = runtime.wrap_command(policy_path, [sys.executable, "-c", script])

    import subprocess
    subprocess.run(argv, capture_output=True, timeout=10)
    assert not (worktree / "x.txt").exists()


def test_wrap_command_requires_managed_policy_directory(tmp_path: Path) -> None:
    runtime = SrtSandboxRuntime(executable=_fake_srt_executable(tmp_path), policy_directory=tmp_path / "policies")
    rogue = tmp_path / "rogue.json"; rogue.write_text("{}")
    with pytest.raises(InfrastructureError, match="managed policy directory"):
        runtime.wrap_command(rogue, ["true"])


def test_wrap_command_blocks_reads_elsewhere_under_home(tmp_path: Path) -> None:
    runtime = SrtSandboxRuntime(executable=_fake_srt_executable(tmp_path), policy_directory=tmp_path / "policies")
    with tempfile.TemporaryDirectory(dir=Path.home()) as root_raw:
        root = Path(root_raw); worktree = root / "worktree"; worktree.mkdir()
        allowed = worktree / "allowed.txt"; allowed.write_text("allowed")
        outside = root / "outside.txt"; outside.write_text("private")
        policy_path = runtime.prepare("reviewer", worktree, worktree)
        script = (
            "from pathlib import Path\n"
            f"assert Path({str(allowed)!r}).read_text() == 'allowed'\n"
            "try:\n"
            f"    Path({str(outside)!r}).read_text()\n"
            "except PermissionError:\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(9)\n"
        )
        result = __import__("subprocess").run(
            runtime.wrap_command(policy_path, [sys.executable, "-c", script]),
            cwd=worktree,
        )
        assert result.returncode == 0
