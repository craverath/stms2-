from pathlib import Path
import sys

from stms.adapters.persistence.langgraph_engine import LocalWorkflowEngine
from stms.adapters.persistence.sqlite_store import SQLiteCheckpointStore
from stms.deterministic.process_runner import ProcessRunner
from stms.domain.models import AllowedEvent, RunMetadata, TestCommand, WorkflowSnapshot


def snapshot() -> WorkflowSnapshot:
    return WorkflowSnapshot(metadata=RunMetadata(run_id="run", repository="/repo", branch_base="main", commit_base="abc", config_digest="digest"))


def test_process_captures_output_and_timeout(tmp_path: Path) -> None:
    runner = ProcessRunner(max_output_bytes=20)
    ok = runner.run(TestCommand(argv=[sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], timeout_seconds=10), tmp_path)
    assert ok.succeeded and ok.stdout.strip() == "out" and ok.stderr.strip() == "err"
    timeout = runner.run(TestCommand(argv=[sys.executable, "-c", "import time; time.sleep(2)"], timeout_seconds=1), tmp_path)
    assert timeout.timed_out and not timeout.succeeded


def test_checkpoint_before_after_is_idempotent_and_lock_recovers(tmp_path: Path) -> None:
    store = SQLiteCheckpointStore(tmp_path / "checkpoint.sqlite"); engine = LocalWorkflowEngine(store); value = snapshot()
    engine.checkpoint_before(value, "operation", "test")
    engine.checkpoint_before(value, "operation", "test")
    engine.checkpoint_after(value, "operation", "artifact")
    assert engine.load("run").metadata.run_id == "run"
    assert store.operation("run", "operation").status.value == "confirmed"
    repository = tmp_path / "repo"; repository.mkdir()
    store.acquire_lock(repository, "one", pid=999999)
    store.acquire_lock(repository, "two")
    store.release_lock(repository, "two")
