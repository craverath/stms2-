"""SQLite source of truth for checkpoints, operation idempotency, and locks."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Iterator

from stms.domain.errors import CompatibilityError, InfrastructureError, LockError
from stms.domain.models import ExternalOperation, OperationStatus, RunState, WorkflowSnapshot

ACTIVE_STATES = {state.value for state in RunState if state not in {RunState.COMPLETED, RunState.FAILED}}


@dataclass(frozen=True)
class SecureDatabaseIdentity:
    """Stable identities for a database and every path component below a root."""

    root: str
    database: tuple[int, int]
    components: tuple[tuple[str, int, int], ...]


def capture_database_identity(database_path: Path, secure_root: Path) -> SecureDatabaseIdentity:
    """Capture a regular database and non-symlink directory chain without following links."""
    root = secure_root.resolve()
    database = database_path.absolute()
    try:
        relative = database.relative_to(root)
    except ValueError as error:
        raise InfrastructureError(
            "Administrative database escapes the repository.",
            "Use a database located below the repository root.",
        ) from error
    try:
        root_metadata = os.lstat(root)
    except OSError as error:
        raise InfrastructureError(
            f"Administrative root is unavailable: {root}.",
            "Restore the repository directory and retry.",
        ) from error
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise InfrastructureError(
            f"Administrative root is unsafe: {root}.",
            "Use a regular repository directory.",
        )
    components: list[tuple[str, int, int]] = [(str(root), root_metadata.st_dev, root_metadata.st_ino)]
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise InfrastructureError(
                f"Administrative database parent is unavailable: {current}.",
                "Restore a regular repository directory and retry.",
            ) from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise InfrastructureError(
                f"Administrative database parent is unsafe: {current}.",
                "Replace symlinks or non-directory components with regular repository directories.",
            )
        components.append((str(current), metadata.st_dev, metadata.st_ino))
    try:
        metadata = os.lstat(database)
    except OSError as error:
        raise InfrastructureError(
            f"Administrative database is unavailable: {database}.",
            "Restore the run database and retry.",
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise InfrastructureError(
            f"Administrative database is unsafe: {database}.",
            "Use a regular SQLite file, not a symlink or special file.",
        )
    return SecureDatabaseIdentity(str(root), (metadata.st_dev, metadata.st_ino), tuple(components))


def readonly_sqlite_uri(database: Path) -> str:
    """See a live WAL when present, otherwise avoid creating SQLite sidecars."""
    wal = database.with_name(database.name + "-wal")
    if wal.is_symlink():
        raise InfrastructureError(
            f"SQLite WAL is unsafe: {wal}.",
            "Replace the symlink with the run's regular WAL file and retry.",
        )
    parameters = "mode=ro" if wal.exists() else "mode=ro&immutable=1"
    return database.resolve().as_uri() + "?" + parameters


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
            connection = sqlite3.connect(readonly_sqlite_uri(database), uri=True)
            row = connection.execute("SELECT state FROM snapshots ORDER BY sequence DESC LIMIT 1").fetchone()
            connection.close()
            if row and row[0] in ACTIVE_STATES:
                return True
        except (InfrastructureError, sqlite3.Error):
            # A malformed partial run is not safe to overwrite; treat it as an
            # active run and direct the user to inspect/resume it.
            return True
    return False


class SQLiteCheckpointStore:
    def __init__(self, database_path: Path, *, secure_identity: SecureDatabaseIdentity | None = None) -> None:
        self.database_path = database_path
        self._secure_identity = secure_identity
        if secure_identity is None:
            database_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._verify_secure_identity()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._verify_secure_identity()
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            # Revalidate after SQLite has opened the file and before yielding a
            # connection to any statement that can write.
            self._verify_secure_identity()
            yield connection
        finally:
            connection.close()

    def _verify_secure_identity(self) -> None:
        expected = self._secure_identity
        if expected is None:
            return
        current = capture_database_identity(self.database_path, Path(expected.root))
        if current != expected:
            raise InfrastructureError(
                "Administrative database path changed during the operation.",
                "Stop concurrent filesystem changes, restore the original regular database, and retry.",
            )

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

    def save_snapshot_and_confirm_operation(
        self,
        snapshot: WorkflowSnapshot,
        operation: ExternalOperation,
    ) -> None:
        """Persist a snapshot and its completed operation atomically."""
        if operation.status != OperationStatus.CONFIRMED:
            raise ValueError("operation must be confirmed")
        payload = snapshot.model_dump_json()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO snapshots(run_id, state, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
                    (snapshot.metadata.run_id, snapshot.state.value, payload, _now()),
                )
                connection.execute(
                    "INSERT INTO operations(run_id, operation_id, kind, status, result_reference, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, operation_id) DO UPDATE SET "
                    "status=excluded.status, result_reference=excluded.result_reference, updated_at=excluded.updated_at",
                    (
                        snapshot.metadata.run_id,
                        operation.id,
                        operation.kind,
                        operation.status.value,
                        operation.result_reference,
                        _now(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

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

    def acquire_administrative_lock(self, repository: Path, owner: str, pid: int | None = None) -> None:
        """Atomically exclude workflow and administrative writers.

        Unlike normal resume locking, administration must not take ownership
        from the target run itself while its process is still alive.
        """
        repository_key = str(repository.resolve()); pid = pid or os.getpid()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT run_id, pid FROM repository_locks WHERE repository=?", (repository_key,)
            ).fetchone()
            if existing and _pid_alive(existing["pid"]):
                connection.execute("ROLLBACK")
                raise LockError(
                    f"Repository is in use by {existing['run_id']} (process {existing['pid']}).",
                    "Wait for that process to exit before running the administrative command.",
                )
            if existing:
                connection.execute("DELETE FROM repository_locks WHERE repository=?", (repository_key,))
            connection.execute(
                "INSERT INTO repository_locks(repository, run_id, pid, acquired_at) VALUES (?, ?, ?, ?)",
                (repository_key, owner, pid, _now()),
            )
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
