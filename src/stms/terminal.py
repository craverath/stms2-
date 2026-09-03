"""Small terminal boundary with serialized, safe rendering."""
from __future__ import annotations

import asyncio
import re
import sys
from typing import TextIO

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\x1b]")


def safe_text(value: str) -> str:
    """Prevent agent output from injecting terminal escapes or Rich markup."""
    return _CONTROL.sub("", value)


class Terminal:
    def __init__(self, *, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout
        self.console = Console(file=self.stream, force_terminal=False, markup=False, highlight=False)
        self._lock = asyncio.Lock()

    @property
    def interactive(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    async def write(self, value: str) -> None:
        async with self._lock:
            self.console.print(Text(safe_text(value)))

    async def ask(self, prompt: str) -> str:
        # One owner of stdin means status writers cannot corrupt an active prompt.
        async with self._lock:
            return input(safe_text(prompt) + " ")

    def markdown(self, value: str) -> None:
        # Markdown is intentional local output only; agent content is wrapped as Text.
        self.console.print(Markdown(safe_text(value)))
