from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

from stms import __version__
from stms.adapters.persistence.sqlite_store import SQLiteCheckpointStore, resumable_run_exists
from stms.cli import app
from stms.domain.models import RunMetadata, RunState, WorkflowSnapshot


def _snapshot(repository: Path, run_id: str, state: RunState) -> WorkflowSnapshot:
    return WorkflowSnapshot(
        metadata=RunMetadata(
            run_id=run_id,
            repository=str(repository.resolve()),
            branch_base="main",
            commit_base="abc",
            config_digest="digest",
        ),
        state=state,
    )


def _run(repository: Path, run_id: str, state: RunState) -> Path:
    root = repository / ".stms" / "estado" / run_id
    SQLiteCheckpointStore(root / "checkpoint.sqlite").save_snapshot(_snapshot(repository, run_id, state))
    return root


def test_version_and_empty_queries_do_not_create_stms(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    version = runner.invoke(app, ["--version"])
    runs = runner.invoke(app, ["runs"])

    assert version.exit_code == 0 and version.output == f"stms {__version__}\n"
    assert runs.exit_code == 0 and "No STMS runs" in runs.output
    assert not (tmp_path / ".stms").exists()


def test_runs_status_and_logs_use_persisted_run_safely(tmp_path: Path, monkeypatch) -> None:
    run = _run(tmp_path, "run-one", RunState.PAUSED)
    (run / "events.jsonl").write_text("event\n", encoding="utf-8")
    tests = run / "tests"; tests.mkdir()
    (tests / "b.log").write_text("second\n", encoding="utf-8")
    (tests / "a.log").write_text("first\n", encoding="utf-8")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    listed = runner.invoke(app, ["runs"])
    status = runner.invoke(app, ["status"])
    logs = runner.invoke(app, ["logs", "run-one"])

    assert listed.exit_code == status.exit_code == logs.exit_code == 0
    assert "run-one state=PAUSED" in listed.output
    assert "state: PAUSED" in status.output and "next action:" in status.output
    assert logs.output.index("events.jsonl") < logs.output.index("tests/a.log") < logs.output.index("tests/b.log")
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_runs_and_status_see_latest_snapshot_still_in_wal(tmp_path: Path, monkeypatch) -> None:
    run = _run(tmp_path, "wal-run", RunState.INTERVIEWING)
    database = run / "checkpoint.sqlite"
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        latest = _snapshot(tmp_path, "wal-run", RunState.PAUSED)
        writer.execute(
            "INSERT INTO snapshots(run_id, state, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
            ("wal-run", RunState.PAUSED.value, latest.model_dump_json(), datetime.now(timezone.utc).isoformat()),
        )
        writer.commit()
        assert database.with_name("checkpoint.sqlite-wal").exists()
        before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
        monkeypatch.chdir(tmp_path)

        listed = CliRunner().invoke(app, ["runs"])
        status = CliRunner().invoke(app, ["status", "wal-run"])

        assert listed.exit_code == status.exit_code == 0
        assert "state=PAUSED" in listed.output
        assert "state: PAUSED" in status.output
        assert resumable_run_exists(tmp_path)
        assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
    finally:
        writer.close()


def test_queries_report_corruption_and_reject_symlink_logs(tmp_path: Path, monkeypatch) -> None:
    corrupt = tmp_path / ".stms" / "estado" / "broken"; corrupt.mkdir(parents=True)
    run = _run(tmp_path, "safe", RunState.FAILED)
    outside = tmp_path / "outside.log"; outside.write_text("secret\n", encoding="utf-8")
    (run / "events.jsonl").symlink_to(outside)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    listed = runner.invoke(app, ["runs"])
    logs = runner.invoke(app, ["logs", "safe"])
    invalid = runner.invoke(app, ["status", "../safe"])

    assert listed.exit_code == 1 and "CORRUPT broken" in listed.output
    assert logs.exit_code == 2 and "symlink" in logs.output
    assert invalid.exit_code == 2 and "Invalid run ID" in invalid.output


def test_logs_reject_symlinked_stms_root(tmp_path: Path, monkeypatch) -> None:
    outside = tmp_path / "outside"; outside.mkdir()
    _run(outside, "escaped", RunState.FAILED)
    (tmp_path / ".stms").symlink_to(outside / ".stms", target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["logs", "escaped"])

    assert result.exit_code == 2 and ".stms is a symlink" in result.output


def test_abort_refuses_live_process_and_handles_replanning(tmp_path: Path, monkeypatch) -> None:
    run = _run(tmp_path, "active", RunState.REPLANNING)
    control = SQLiteCheckpointStore(tmp_path / ".stms" / "control.sqlite")
    control.acquire_lock(tmp_path, "active", pid=os.getpid())
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    refused = runner.invoke(app, ["abort", "active", "--yes"])
    assert refused.exit_code == 2 and "process" in refused.output

    control.release_lock(tmp_path, "active")
    aborted = runner.invoke(app, ["abort", "active", "--yes"])
    assert aborted.exit_code == 0 and "FAILED" in aborted.output
    assert SQLiteCheckpointStore(run / "checkpoint.sqlite").latest_snapshot("active").state == RunState.FAILED
    assert '"event_type": "abort"' in (run / "events.jsonl").read_text(encoding="utf-8")

    repeated = runner.invoke(app, ["abort", "active", "--yes"])
    assert repeated.exit_code == 0 and "already FAILED" in repeated.output


def test_abort_retries_after_atomic_confirmation_failure(tmp_path: Path, monkeypatch) -> None:
    run = _run(tmp_path, "recoverable", RunState.PAUSED)
    database = run / "checkpoint.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TRIGGER fail_abort_confirmation
            BEFORE UPDATE OF status ON operations
            WHEN NEW.status = 'confirmed'
            BEGIN
                SELECT RAISE(ABORT, 'simulated confirmation failure');
            END
        """)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    failed = runner.invoke(app, ["abort", "recoverable", "--yes"])

    store = SQLiteCheckpointStore(database)
    assert failed.exit_code == 1
    assert store.latest_snapshot("recoverable").state == RunState.PAUSED
    assert store.operation("recoverable", "admin-abort-2").status.value == "pending"

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_abort_confirmation")

    retried = runner.invoke(app, ["abort", "recoverable", "--yes"])

    assert retried.exit_code == 0
    assert store.latest_snapshot("recoverable").state == RunState.FAILED
    assert store.operation("recoverable", "admin-abort-2").status.value == "confirmed"
    assert '"event_type": "abort"' in (run / "events.jsonl").read_text(encoding="utf-8")


def test_clean_dry_run_confirmation_and_safe_targets(tmp_path: Path, monkeypatch) -> None:
    terminal = _run(tmp_path, "terminal", RunState.COMPLETED)
    active = _run(tmp_path, "active", RunState.IMPLEMENTING)
    outside = tmp_path / "outside"; outside.mkdir()
    (tmp_path / ".stms" / "estado" / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    preview = runner.invoke(app, ["clean", "--dry-run"])
    assert preview.exit_code == 1 and "Would remove: terminal" in preview.output
    assert terminal.exists()

    cancelled = runner.invoke(app, ["clean"], input="n\n")
    assert cancelled.exit_code == 1 and terminal.exists()

    cleaned = runner.invoke(app, ["clean", "--yes"])
    assert cleaned.exit_code == 1
    assert not terminal.exists()
    assert active.exists() and outside.exists()


def test_clean_refuses_ancestor_exchange_at_removal_boundary(tmp_path: Path, monkeypatch) -> None:
    run = _run(tmp_path, "terminal", RunState.COMPLETED)
    (run / "original-marker").write_text("keep", encoding="utf-8")
    root = tmp_path / ".stms" / "estado"
    displaced = tmp_path / ".stms" / "estado-displaced"
    external_root = tmp_path / "external-estado"
    external_run = external_root / "terminal"; external_run.mkdir(parents=True)
    (external_run / "external-marker").write_text("keep", encoding="utf-8")
    from stms.application import run_admin

    real_remove = run_admin._remove_tree_at
    exchanged = False

    def exchange_ancestor(*args, **kwargs) -> None:
        nonlocal exchanged
        exchanged = True
        root.rename(displaced)
        root.symlink_to(external_root, target_is_directory=True)
        real_remove(*args, **kwargs)

    monkeypatch.setattr(run_admin, "_remove_tree_at", exchange_ancestor)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["clean", "--yes"])

    assert exchanged
    assert result.exit_code == 1 and "path changed" in result.output
    assert (displaced / "terminal" / "original-marker").read_text(encoding="utf-8") == "keep"
    assert (external_run / "external-marker").read_text(encoding="utf-8") == "keep"
