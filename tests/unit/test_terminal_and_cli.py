from typer.testing import CliRunner

from stms.cli import app
from stms.terminal import safe_text


def test_agent_terminal_content_is_neutralized() -> None:
    assert safe_text("\x1b[31m[bold]unsafe") == "[31m[bold]unsafe"


def test_start_requires_exactly_one_input() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["start"]).exit_code == 2
    assert runner.invoke(app, ["start", "request", "--file", "missing.md"]).exit_code == 2
