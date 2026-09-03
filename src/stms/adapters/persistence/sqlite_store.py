"""SQLite source of truth for checkpoints, operation idempotency, and locks."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from stms.domain.errors import CompatibilityError, LockError
from stms.domain.models import ExternalOperation, OperationStatus, RunState, WorkflowSnapshot

ACTIVE_STATES = {state.value for state in RunState if state not in {RunState.COMPLETED, RunState.FAILED}}


def resumable_run_exists(repository: Path) -> bool:
    """Read persisted run snapshots without creating SQLite/WAL files.

    Startup must reject a paused run even after its original process has exited.
    This intentionally uses SQLite's read-only URI rather than the writable store
    constructor, because preflight has no authorization to alter the repository.
    """
    root = repository.resolve() / ".stms" / "estado"
    if not root.exists():
        return False
    for database in root.glob("*/checkpoint.sqlite"):
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            row = connection.execute("SELECT state FROM snapshots ORDER BY sequence DESC LIMIT 1").fetchone()
            connection.close()
            if row and row[0] in ACTIVE_STATES:
                return True
        except sqlite3.Error:
            # A malformed partial run is not safe to overwrite; treat it as an
            # active run and direct the user to inspect/resume it.
            return True
    return False


class SQLiteCheckpointStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    run_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    state TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snapshots_run_sequence ON snapshots(run_id, sequence DESC);
                CREATE TABLE IF NOT EXISTS operations (
                    run_id TEXT NOT NULL, operation_id TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, result_reference TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS repository_locks (
                    repository TEXT PRIMARY KEY, run_id TEXT NOT NULL, pid INTEGER NOT NULL, acquired_at TEXT NOT NULL
                );
            """)

    def save_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        payload = snapshot.model_dump_json()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO snapshots(run_id, state, snapshot_json, created_at) VALUES (?, ?, ?, ?)", (snapshot.metadata.run_id, snapshot.state.value, payload, _now()))
            connection.execute("COMMIT")

    def latest_snapshot(self, run_id: str) -> WorkflowSnapshot | None:
        with self._connection() as connection:
            row = connection.execute("SELECT snapshot_json FROM snapshots WHERE run_id = ? ORDER BY sequence DESC LIMIT 1", (run_id,)).fetchone()
        return WorkflowSnapshot.model_validate_json(row["snapshot_json"]) if row else None

    def latest_resumable(self, repository: Path) -> WorkflowSnapshot | None:
        with self._connection() as connection:
            rows = connection.execute("SELECT snapshot_json FROM snapshots ORDER BY sequence DESC").fetchall()
        for row in rows:
            snapshot = WorkflowSnapshot.model_validate_json(row["snapshot_json"])
            if Path(snapshot.metadata.repository).resolve() == repository.resolve() and snapshot.state.value in ACTIVE_STATES:
                return snapshot
        return None

    def record_operation(self, run_id: str, operation: ExternalOperation) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO operations(run_id, operation_id, kind, status, result_reference, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, operation_id) DO UPDATE SET status=excluded.status, result_reference=excluded.result_reference, updated_at=excluded.updated_at", (run_id, operation.id, operation.kind, operation.status.value, operation.result_reference, _now()))
            connection.execute("COMMIT")

    def operation(self, run_id: str, operation_id: str) -> ExternalOperation | None:
        with self._connection() as connection:
            row = connection.execute("SELECT operation_id, kind, status, result_reference FROM operations WHERE run_id=? AND operation_id=?", (run_id, operation_id)).fetchone()
        return ExternalOperation(id=row["operation_id"], kind=row["kind"], status=OperationStatus(row["status"]), result_reference=row["result_reference"]) if row else None

    def acquire_lock(self, repository: Path, run_id: str, pid: int | None = None) -> None:
        repository_key = str(repository.resolve()); pid = pid or os.getpid()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT run_id, pid FROM repository_locks WHERE repository=?", (repository_key,)).fetchone()
            if existing and existing["run_id"] != run_id:
                if _pid_alive(existing["pid"]):
                    connection.execute("ROLLBACK")
                    raise LockError(f"Repository already has active run {existing['run_id']}.", "Resume or finish that run before starting another one.")
                connection.execute("DELETE FROM repository_locks WHERE repository=?", (repository_key,))
            connection.execute("INSERT INTO repository_locks(repository, run_id, pid, acquired_at) VALUES (?, ?, ?, ?) ON CONFLICT(repository) DO UPDATE SET run_id=excluded.run_id, pid=excluded.pid, acquired_at=excluded.acquired_at", (repository_key, run_id, pid, _now()))
            connection.execute("COMMIT")

    def release_lock(self, repository: Path, run_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM repository_locks WHERE repository=? AND run_id=?", (str(repository.resolve()), run_id))

    def verify_compatibility(self, snapshot: WorkflowSnapshot, *, config_digest: str, workflow_version: str, prompt_digest: str, adapter_versions: dict[str, str]) -> None:
        metadata = snapshot.metadata
        mismatches = []
        if metadata.config_digest != config_digest: mismatches.append("configuration digest")
        if metadata.workflow_version != workflow_version: mismatches.append("workflow version")
        if metadata.prompt_digest != prompt_digest: mismatches.append("prompt digest")
        if metadata.adapter_versions != adapter_versions: mismatches.append("adapter versions")
        if mismatches:
            raise CompatibilityError("Run cannot be resumed because " + ", ".join(mismatches) + " changed.", "Use a compatible STMS version/configuration or start a new run; migrations are not automatic.")


def _pid_alive(pid: int) -> bool:
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    else: return True


def _now() -> str: return datetime.now(timezone.utc).isoformat()
