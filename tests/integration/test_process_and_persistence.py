from pathlib import Path
import sqlite3
import sys

import pytest

from stms.adapters.persistence.langgraph_engine import LocalWorkflowEngine
from stms.adapters.persistence.sqlite_store import SQLiteCheckpointStore, capture_database_identity
from stms.deterministic.process_runner import ProcessRunner
from stms.domain.errors import InfrastructureError
from stms.domain.models import AllowedEvent, RunMetadata, RunState, TestCommand, WorkflowSnapshot


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


def test_secure_checkpoint_store_rejects_database_replacement(tmp_path: Path) -> None:
    database = tmp_path / "run" / "checkpoint.sqlite"
    original = SQLiteCheckpointStore(database)
    original.save_snapshot(snapshot())
    identity = capture_database_identity(database, tmp_path)
    secured = SQLiteCheckpointStore(database, secure_identity=identity)

    database.rename(database.with_suffix(".original"))
    replacement = SQLiteCheckpointStore(database)
    paused = snapshot().model_copy(update={"state": RunState.PAUSED})
    replacement.save_snapshot(paused)

    with pytest.raises(InfrastructureError, match="path changed"):
        secured.save_snapshot(snapshot().model_copy(update={"state": RunState.FAILED}))
    assert replacement.latest_snapshot("run").state.value == "PAUSED"


def test_secure_control_store_rejects_database_replacement(tmp_path: Path) -> None:
    database = tmp_path / ".stms" / "control.sqlite"
    SQLiteCheckpointStore(database)
    identity = capture_database_identity(database, tmp_path)
    secured = SQLiteCheckpointStore(database, secure_identity=identity)

    database.rename(database.with_suffix(".original"))
    replacement = SQLiteCheckpointStore(database)
    repository = tmp_path / "repo"; repository.mkdir()

    with pytest.raises(InfrastructureError, match="path changed"):
        secured.acquire_administrative_lock(repository, "administrator")
    replacement.acquire_lock(repository, "replacement", pid=999999)
    replacement.release_lock(repository, "replacement")


def test_secure_store_revalidates_replacement_after_sqlite_open(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "run" / "checkpoint.sqlite"
    original = SQLiteCheckpointStore(database)
    original.save_snapshot(snapshot())
    identity = capture_database_identity(database, tmp_path)
    secured = SQLiteCheckpointStore(database, secure_identity=identity)
    replacement_path = tmp_path / "replacement.sqlite"
    replacement = SQLiteCheckpointStore(replacement_path)
    replacement.save_snapshot(snapshot().model_copy(update={"state": RunState.PAUSED}))
    displaced = database.with_suffix(".displaced")
    real_connect = sqlite3.connect
    exchanged = False

    def connect_and_exchange(*args, **kwargs):
        nonlocal exchanged
        connection = real_connect(*args, **kwargs)
        if not exchanged and Path(args[0]) == database:
            exchanged = True
            database.rename(displaced)
            replacement_path.rename(database)
        return connection

    monkeypatch.setattr("stms.adapters.persistence.sqlite_store.sqlite3.connect", connect_and_exchange)

    with pytest.raises(InfrastructureError, match="path changed"):
        secured.save_snapshot(snapshot().model_copy(update={"state": RunState.FAILED}))
    assert exchanged
    with real_connect(database) as connection:
        payload = connection.execute(
            "SELECT snapshot_json FROM snapshots ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
    assert WorkflowSnapshot.model_validate_json(payload).state == RunState.PAUSED
