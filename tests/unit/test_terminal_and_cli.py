from typer.testing import CliRunner

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


def test_terminal_ask_uses_async_prompt_session() -> None:
    import asyncio
    session = _PromptSession()
    assert asyncio.run(Terminal(prompt_session=session).ask("Question")) == "answer"
    assert session.prompts == ["Question "]
