from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from stms.application.configuration import configuration_example
from stms.cli import app
from stms.terminal import safe_text
from stms.terminal import Terminal


class _PromptSession:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def prompt_async(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "answer"


def test_agent_terminal_content_is_neutralized() -> None:
    assert safe_text("\x1b[31m[bold]unsafe") == "[31m[bold]unsafe"


def test_start_requires_exactly_one_input() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["start"]).exit_code == 2
    assert runner.invoke(app, ["start", "request", "--file", "missing.md"]).exit_code == 2


def test_init_creates_configuration_without_overwriting_it(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    created = runner.invoke(app, ["init"])
    config_path = tmp_path / "stms.yml"

    assert created.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == configuration_example()

    config_path.write_text("user configuration\n", encoding="utf-8")
    repeated = runner.invoke(app, ["init"])

    assert repeated.exit_code == 2
    assert "already exists" in repeated.output
    assert config_path.read_text(encoding="utf-8") == "user configuration\n"


def test_update_upgrades_installed_tool_with_uv(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("stms.cli.shutil.which", lambda executable: "/usr/local/bin/uv")

    def run(command: list[str], *, check: bool) -> CompletedProcess:
        commands.append(command)
        return CompletedProcess(command, 0)

    monkeypatch.setattr("stms.cli.subprocess.run", run)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0
    assert commands == [["/usr/local/bin/uv", "tool", "upgrade", "stms"]]


def test_update_reports_missing_uv(monkeypatch) -> None:
    monkeypatch.setattr("stms.cli.shutil.which", lambda executable: None)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 2
    assert "uv is not installed" in result.output


def test_uninstall_removes_installed_tool_with_uv(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("stms.cli.shutil.which", lambda executable: "/usr/local/bin/uv")

    def run(command: list[str], *, check: bool) -> CompletedProcess:
        commands.append(command)
        return CompletedProcess(command, 0)

    monkeypatch.setattr("stms.cli.subprocess.run", run)

    result = CliRunner().invoke(app, ["uninstall"])

    assert result.exit_code == 0
    assert commands == [["/usr/local/bin/uv", "tool", "uninstall", "stms"]]


def test_terminal_ask_uses_async_prompt_session() -> None:
    import asyncio
    session = _PromptSession()
    assert asyncio.run(Terminal(prompt_session=session).ask("Question")) == "answer"
    assert session.prompts == ["Question "]
