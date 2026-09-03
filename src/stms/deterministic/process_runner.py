"""Subprocess execution with canonical paths and process-group cleanup."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time

from stms.domain.errors import SecurityError
from stms.domain.models import ProcessResult, TestCommand


class ProcessRunner:
    def __init__(self, *, max_output_bytes: int = 1_000_000) -> None:
        self.max_output_bytes = max_output_bytes

    def run(self, command: TestCommand, cwd: Path, *, repository: Path | None = None, allow_shell: bool = False, cancel_event: object | None = None) -> ProcessResult:
        resolved_cwd = cwd.resolve(); root = (repository or resolved_cwd).resolve()
        if root != resolved_cwd and root not in resolved_cwd.parents:
            raise SecurityError("Process working directory escapes repository.", "Use a canonical working directory within the repository.")
        if command.shell and not allow_shell:
            raise SecurityError("shell: true was not explicitly approved.", "Approve shell execution in the frozen plan before running this command.")
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR"}}
        environment.update(command.resolved_environment(dict(os.environ)))
        started_at = datetime.now(timezone.utc); started = time.monotonic()
        args: list[str] | str = " ".join(command.argv) if command.shell else command.argv
        process = subprocess.Popen(args, cwd=resolved_cwd, env=environment, shell=command.shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, start_new_session=True)
        timed_out = cancelled = False
        try:
            stdout, stderr = process.communicate(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True; self._terminate_tree(process); stdout, stderr = process.communicate()
        except KeyboardInterrupt:
            cancelled = True; self._terminate_tree(process); stdout, stderr = process.communicate(); raise
        duration = time.monotonic() - started
        stdout_text, stdout_truncated = _truncate(stdout, self.max_output_bytes)
        stderr_text, stderr_truncated = _truncate(stderr, self.max_output_bytes)
        return ProcessResult(argv=command.argv, cwd=str(resolved_cwd), started_at=started_at, duration_seconds=duration, exit_code=None if process.returncode is not None and process.returncode < 0 else process.returncode, signal=-process.returncode if process.returncode is not None and process.returncode < 0 else None, timed_out=timed_out, cancelled=cancelled, stdout=stdout_text, stderr=stderr_text, truncated=stdout_truncated or stderr_truncated)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None: return
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: return
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass


def _truncate(value: bytes, max_bytes: int) -> tuple[str, bool]:
    truncated = len(value) > max_bytes
    if truncated: value = value[:max_bytes] + b"\n[TRUNCATED]"
    return value.decode("utf-8", errors="replace"), truncated
