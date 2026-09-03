"""Read-only run inspection and explicitly authorized administration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import stat

from pydantic import TypeAdapter, ValidationError

from stms.adapters.persistence.artifact_store import LocalArtifactStore
from stms.adapters.persistence.sqlite_store import (
    SecureDatabaseIdentity,
    SQLiteCheckpointStore,
    capture_database_identity,
    readonly_sqlite_uri,
)
from stms.domain.errors import InfrastructureError
from stms.domain.events import NormalizedEvent
from stms.domain.models import (
    AllowedEvent,
    ExternalOperation,
    OperationStatus,
    RunId,
    RunState,
    WorkflowSnapshot,
)
from stms.domain.states import transition


_RUN_ID = TypeAdapter(RunId)
_TERMINAL_STATES = {RunState.COMPLETED, RunState.FAILED}


class RunCommandError(Exception):
    """An actionable CLI error with its stable public exit code."""

    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    snapshot: WorkflowSnapshot
    sequence: int
    checkpoint_at: str
    pending_operations: tuple[str, ...]
    directory: Path
    database_identity: SecureDatabaseIdentity

    @property
    def progress(self) -> str:
        snapshot = self.snapshot
        return (
            f"tasks={len(snapshot.completed_task_ids)} "
            f"waves={snapshot.completed_waves} attempt={snapshot.attempt}"
        )


@dataclass(frozen=True)
class RunIssue:
    run_id: str
    message: str


@dataclass(frozen=True)
class LockRecord:
    run_id: str
    pid: int
    acquired_at: str

    @property
    def alive(self) -> bool:
        return pid_alive(self.pid)


@dataclass(frozen=True)
class CleanResult:
    candidates: tuple[RunRecord, ...]
    removed: tuple[str, ...]
    ignored: tuple[str, ...]
    errors: tuple[str, ...]


def validate_run_id(value: str) -> str:
    try:
        return _RUN_ID.validate_python(value)
    except ValidationError as error:
        raise RunCommandError(
            f"Invalid run ID {value!r}; use only letters, digits, '_' or '-' (maximum 128 characters).",
            exit_code=2,
        ) from error


def inspect_runs(repository: Path) -> tuple[list[RunRecord], list[RunIssue]]:
    """Read every direct run database without creating SQLite or WAL files."""
    stms_root = repository.resolve() / ".stms"
    if stms_root.is_symlink():
        return [], [RunIssue(".stms", "control directory is a symlink")]
    root = _runs_root(repository)
    if not root.exists():
        return [], []
    if root.is_symlink() or not root.is_dir():
        return [], [RunIssue(".stms/estado", "run root is not a regular directory")]
    records: list[RunRecord] = []
    issues: list[RunIssue] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as error:
        return [], [RunIssue(".stms/estado", f"cannot list run root: {error}")]
    for directory in children:
        if directory.is_symlink():
            issues.append(RunIssue(directory.name, "run entry is a symlink"))
            continue
        if not directory.is_dir():
            continue
        try:
            validate_run_id(directory.name)
            records.append(_read_run(repository, directory, directory.name))
        except RunCommandError as error:
            issues.append(RunIssue(directory.name, str(error)))
    records.sort(key=lambda record: (_checkpoint_datetime(record.checkpoint_at), record.sequence), reverse=True)
    return records, issues


def find_run(repository: Path, run_id: str | None = None) -> RunRecord:
    if run_id is not None:
        identifier = validate_run_id(run_id)
        if (repository.resolve() / ".stms").is_symlink():
            raise RunCommandError(".stms is a symlink; refusing unsafe access.", exit_code=2)
        root = _runs_root(repository)
        if root.is_symlink():
            raise RunCommandError("Run root .stms/estado is a symlink; refusing unsafe access.", exit_code=2)
        directory = root / identifier
        if directory.is_symlink():
            raise RunCommandError(f"Run {identifier!r} is a symlink; refusing unsafe access.", exit_code=2)
        if not directory.is_dir():
            raise RunCommandError(f"Run {identifier!r} does not exist.", exit_code=2)
        return _read_run(repository, directory, identifier)
    records, issues = inspect_runs(repository)
    if issues:
        details = "; ".join(f"{issue.run_id}: {issue.message}" for issue in issues)
        raise RunCommandError(f"Cannot select the newest run because run data is corrupt: {details}", exit_code=1)
    if not records:
        raise RunCommandError("No STMS runs were found in this repository.", exit_code=2)
    return records[0]


def read_logs(repository: Path, run_id: str) -> list[tuple[str, str]]:
    record = find_run(repository, run_id)
    root = record.directory
    sections: list[tuple[str, str]] = []
    events = root / "events.jsonl"
    if events.exists() or events.is_symlink():
        sections.append(("events.jsonl", _read_safe_log(root, events)))
    tests = root / "tests"
    if tests.is_symlink():
        raise RunCommandError(f"Run {record.run_id!r} has a symlinked tests directory; refusing unsafe access.", exit_code=2)
    if tests.exists():
        if not tests.is_dir():
            raise RunCommandError(f"Run {record.run_id!r} has an invalid tests entry.", exit_code=1)
        try:
            paths = sorted(tests.glob("*.log"), key=lambda path: path.name)
        except OSError as error:
            raise RunCommandError(f"Could not list logs for run {record.run_id!r}: {error}", exit_code=1) from error
        for path in paths:
            sections.append((f"tests/{path.name}", _read_safe_log(root, path)))
    return sections


def repository_lock(repository: Path) -> LockRecord | None:
    lock, _ = _repository_lock_with_identity(repository)
    return lock


def _repository_lock_with_identity(
    repository: Path,
) -> tuple[LockRecord | None, SecureDatabaseIdentity | None]:
    stms_root = repository.resolve() / ".stms"
    if stms_root.is_symlink():
        raise RunCommandError(".stms is a symlink; refusing unsafe control database access.", exit_code=1)
    database = stms_root / "control.sqlite"
    if not database.exists():
        return None, None
    if database.is_symlink() or not database.is_file():
        raise RunCommandError("Control database is not a regular file.", exit_code=1)
    try:
        identity = capture_database_identity(database, repository.resolve())
        with _readonly_connection(database, identity=identity) as connection:
            row = connection.execute(
                "SELECT run_id, pid, acquired_at FROM repository_locks WHERE repository=?",
                (str(repository.resolve()),),
            ).fetchone()
    except (InfrastructureError, OSError, sqlite3.Error) as error:
        raise RunCommandError(f"Control database is unreadable: {error}", exit_code=1) from error
    lock = LockRecord(str(row["run_id"]), int(row["pid"]), str(row["acquired_at"])) if row else None
    return lock, identity


def abort_run(repository: Path, run_id: str) -> tuple[WorkflowSnapshot, bool]:
    """Abort an inactive run under a repository-wide administrative lock."""
    record = find_run(repository, run_id)
    if record.snapshot.state == RunState.COMPLETED:
        raise RunCommandError(f"Run {record.run_id!r} is COMPLETED and cannot be aborted.", exit_code=2)

    control = _administrative_store(repository)
    owner = f"__admin-abort-{os.getpid()}-{record.run_id}"
    try:
        control.acquire_administrative_lock(repository, owner)
    except Exception as error:
        raise RunCommandError(f"Cannot abort run {record.run_id!r}: {error}", exit_code=2) from error
    try:
        # Re-read after exclusion so a process cannot race the state check.
        current = find_run(repository, record.run_id)
        if current.snapshot.state == RunState.FAILED:
            return current.snapshot, False
        if current.snapshot.state == RunState.COMPLETED:
            raise RunCommandError(f"Run {record.run_id!r} completed before it could be aborted.", exit_code=2)
        database = current.directory / "checkpoint.sqlite"
        store = SQLiteCheckpointStore(database, secure_identity=current.database_identity)
        operation_id = f"admin-abort-{current.sequence + 1}"
        store.record_operation(
            current.run_id,
            ExternalOperation(id=operation_id, kind="abort", status=OperationStatus.PENDING),
        )
        snapshot = transition(current.snapshot, AllowedEvent.ABORT).model_copy(
            update={"updated_at": datetime.now(timezone.utc)}
        )
        store.save_snapshot(snapshot)
        store.record_operation(
            current.run_id,
            ExternalOperation(
                id=operation_id,
                kind="abort",
                status=OperationStatus.CONFIRMED,
                result_reference="user_aborted_from_cli",
            ),
        )
        artifacts = LocalArtifactStore(repository, current.run_id)
        artifacts.write_json("state.json", snapshot.model_dump(mode="json"))
        artifacts.append_event(
            NormalizedEvent(
                run_id=current.run_id,
                event_type="abort",
                phase=snapshot.phase,
                state=snapshot.state,
                task_id=snapshot.task_id,
                attempt=snapshot.attempt,
                review_round=snapshot.review_round,
                result="user_aborted_from_cli",
            )
        )
        return snapshot, True
    except RunCommandError:
        raise
    except (InfrastructureError, OSError, sqlite3.Error, ValueError) as error:
        raise RunCommandError(f"Could not persist abort for run {record.run_id!r}: {error}", exit_code=1) from error
    finally:
        control.release_lock(repository, owner)


def clean_runs(
    repository: Path,
    *,
    dry_run: bool,
    only_run_ids: frozenset[str] | None = None,
) -> CleanResult:
    """List or remove only repository-owned terminal direct run directories."""
    if dry_run:
        return _clean_runs(repository, dry_run=True, only_run_ids=only_run_ids)
    control = _administrative_store(repository)
    owner = f"__admin-clean-{os.getpid()}"
    try:
        control.acquire_administrative_lock(repository, owner)
    except Exception as error:
        raise RunCommandError(f"Cannot clean while the repository is in use: {error}", exit_code=1) from error
    try:
        return _clean_runs(
            repository,
            dry_run=False,
            only_run_ids=only_run_ids,
            administrative_owner=owner,
        )
    finally:
        control.release_lock(repository, owner)


def _clean_runs(
    repository: Path,
    *,
    dry_run: bool,
    only_run_ids: frozenset[str] | None,
    administrative_owner: str | None = None,
) -> CleanResult:
    records, issues = inspect_runs(repository)
    ignored = [f"{issue.run_id}: {issue.message}" for issue in issues]
    errors = list(ignored)
    try:
        lock = repository_lock(repository)
    except RunCommandError as error:
        lock = None
        errors.append(str(error))

    candidates: list[RunRecord] = []
    selected_records: set[str] = set()
    for record in records:
        if only_run_ids is not None and record.run_id not in only_run_ids:
            continue
        selected_records.add(record.run_id)
        if Path(record.snapshot.metadata.repository).resolve() != repository.resolve():
            message = f"{record.run_id}: belongs to another repository"
            ignored.append(message)
            errors.append(message)
        elif record.snapshot.state not in _TERMINAL_STATES:
            message = f"{record.run_id}: active state {record.snapshot.state.value}"
            ignored.append(message)
            if not dry_run and only_run_ids is not None:
                errors.append(f"{message}; removal was not performed")
        elif lock is not None and lock.alive and lock.run_id != administrative_owner:
            ignored.append(f"{record.run_id}: repository process {lock.pid} ({lock.run_id}) is alive")
        else:
            candidates.append(record)

    if not dry_run and only_run_ids is not None:
        for missing in sorted(only_run_ids - selected_records):
            errors.append(f"{missing}: disappeared before removal")

    removed: list[str] = []
    if not dry_run:
        for record in candidates:
            try:
                _remove_run_directory(repository, record)
                removed.append(record.run_id)
            except (InfrastructureError, OSError) as error:
                errors.append(f"{record.run_id}: removal failed: {error}")
    return CleanResult(tuple(candidates), tuple(removed), tuple(ignored), tuple(errors))


def next_action(snapshot: WorkflowSnapshot) -> str:
    actions = {
        RunState.INTERVIEWING: "continue planning with stms resume",
        RunState.PLAN_PENDING_APPROVAL: "review and approve the plan with stms resume",
        RunState.IMPLEMENTING: "continue implementation with stms resume",
        RunState.TESTING: "continue tests with stms resume",
        RunState.REVIEWING: "continue review with stms resume",
        RunState.FINAL_APPROVAL: "provide the final decision with stms resume",
        RunState.MERGING: "continue integration with stms resume",
        RunState.PAUSED: "inspect the pause reason, then run stms resume",
        RunState.REPLANNING: "continue replanning with stms resume",
        RunState.COMPLETED: "no action; the run is complete",
        RunState.FAILED: "inspect logs or clean the preserved artifacts",
    }
    return actions[snapshot.state]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _runs_root(repository: Path) -> Path:
    return repository.resolve() / ".stms" / "estado"


def _administrative_store(repository: Path) -> SQLiteCheckpointStore:
    # Validate an existing control database with the read-only path before a
    # writable constructor can initialize or follow anything unsafe.
    database = repository.resolve() / ".stms" / "control.sqlite"
    try:
        _, identity = _repository_lock_with_identity(repository)
        if identity is None:
            try:
                identity = _create_control_database(repository, database)
            except FileExistsError:
                # Another creator won the race. Validate its schema and bind
                # the writer to the exact file it validated.
                _, identity = _repository_lock_with_identity(repository)
        assert identity is not None
        return SQLiteCheckpointStore(database, secure_identity=identity)
    except RunCommandError:
        raise
    except (InfrastructureError, OSError, sqlite3.Error) as error:
        raise RunCommandError(f"Control database cannot be opened safely: {error}", exit_code=1) from error


def _remove_run_directory(repository: Path, record: RunRecord) -> None:
    """Remove one run relative to verified directory descriptors."""
    root = _runs_root(repository)
    expected_root = _component_identity(record.database_identity, root)
    expected_run = _component_identity(record.database_identity, record.directory)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    root_descriptor = os.open(root, flags)
    try:
        _verify_open_directory(root_descriptor, expected_root, root)
        run_descriptor = os.open(record.run_id, flags, dir_fd=root_descriptor)
        try:
            _verify_open_directory(run_descriptor, expected_run, record.directory)
            _remove_tree_at(
                root_descriptor,
                root,
                record.run_id,
                expected_root,
                expected_run,
            )
        finally:
            os.close(run_descriptor)
    finally:
        os.close(root_descriptor)


def _remove_tree_at(
    root_descriptor: int,
    root: Path,
    run_id: str,
    expected_root: tuple[int, int],
    expected_run: tuple[int, int],
) -> None:
    """Revalidate at the deletion boundary, then use symlink-safe rmtree."""
    _verify_directory_path(root, expected_root)
    metadata = os.stat(run_id, dir_fd=root_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != expected_run:
        raise InfrastructureError(
            f"Run directory {run_id!r} changed before removal.",
            "Retry clean after concurrent filesystem changes stop.",
        )
    if not shutil.rmtree.avoids_symlink_attacks:
        raise InfrastructureError(
            "This platform cannot remove run directories without following symlinks.",
            "Run clean on a supported macOS or Linux Python runtime.",
        )
    shutil.rmtree(run_id, dir_fd=root_descriptor)


def _component_identity(identity: SecureDatabaseIdentity, path: Path) -> tuple[int, int]:
    expected = next((item[1:] for item in identity.components if item[0] == str(path)), None)
    if expected is None:
        raise InfrastructureError(
            f"Run path was not part of the validated database path: {path}.",
            "Inspect the run layout and retry.",
        )
    return expected


def _verify_open_directory(descriptor: int, expected: tuple[int, int], path: Path) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != expected:
        raise InfrastructureError(
            f"Run path changed while it was opened: {path}.",
            "Retry clean after concurrent filesystem changes stop.",
        )
    _verify_directory_path(path, expected)


def _verify_directory_path(path: Path, expected: tuple[int, int]) -> None:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != expected:
        raise InfrastructureError(
            f"Run path changed before removal: {path}.",
            "Retry clean after concurrent filesystem changes stop.",
        )


def _create_control_database(repository: Path, database: Path) -> SecureDatabaseIdentity:
    """Create control.sqlite relative to an opened, verified .stms directory."""
    root = repository.resolve()
    parent = root / ".stms"
    parent_metadata = os.lstat(parent)
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise InfrastructureError(
            ".stms changed before the control database could be created.",
            "Restore a regular .stms directory and retry.",
        )
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    parent_descriptor = os.open(parent, directory_flags)
    try:
        opened_parent = os.fstat(parent_descriptor)
        expected_parent = (parent_metadata.st_dev, parent_metadata.st_ino)
        if (opened_parent.st_dev, opened_parent.st_ino) != expected_parent:
            raise InfrastructureError(
                ".stms changed while the control database was opened.",
                "Stop concurrent filesystem changes and retry.",
            )
        file_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        descriptor = os.open(database.name, file_flags, 0o600, dir_fd=parent_descriptor)
        try:
            opened_database = os.fstat(descriptor)
            expected_database = (opened_database.st_dev, opened_database.st_ino)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    identity = capture_database_identity(database, root)
    parent_identity = next(
        (item[1:] for item in identity.components if item[0] == str(parent)),
        None,
    )
    if identity.database != expected_database or parent_identity != expected_parent:
        raise InfrastructureError(
            "Control database path changed immediately after creation.",
            "Stop concurrent filesystem changes and retry.",
        )
    return identity


def _read_run(repository: Path, directory: Path, run_id: str) -> RunRecord:
    database = directory / "checkpoint.sqlite"
    if database.is_symlink() or not database.is_file():
        raise RunCommandError("missing or unsafe checkpoint.sqlite", exit_code=1)
    try:
        identity = capture_database_identity(database, repository.resolve())
        with _readonly_connection(database, identity=identity) as connection:
            row = connection.execute(
                "SELECT sequence, snapshot_json, created_at FROM snapshots "
                "WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if row is None:
                raise RunCommandError("checkpoint database has no snapshot", exit_code=1)
            operation_rows = connection.execute(
                "SELECT operation_id, kind, status FROM operations "
                "WHERE run_id=? AND status IN (?, ?) ORDER BY updated_at, operation_id",
                (run_id, OperationStatus.PENDING.value, OperationStatus.STARTED.value),
            ).fetchall()
        snapshot = WorkflowSnapshot.model_validate_json(row["snapshot_json"])
        checkpoint_at = str(row["created_at"])
        parsed_checkpoint = datetime.fromisoformat(checkpoint_at)
        if parsed_checkpoint.tzinfo is None:
            raise ValueError("checkpoint timestamp has no timezone")
    except RunCommandError:
        raise
    except (InfrastructureError, OSError, sqlite3.Error, ValidationError, ValueError) as error:
        raise RunCommandError(f"corrupt or unreadable checkpoint database: {error}", exit_code=1) from error
    if snapshot.metadata.run_id != run_id:
        raise RunCommandError(
            f"checkpoint run ID {snapshot.metadata.run_id!r} does not match directory", exit_code=1
        )
    pending = tuple(
        f"{item['operation_id']} ({item['kind']}:{item['status']})" for item in operation_rows
    )
    return RunRecord(
        run_id,
        snapshot,
        int(row["sequence"]),
        checkpoint_at,
        pending,
        directory,
        identity,
    )


class _ReadOnlyConnection:
    def __init__(self, path: Path, *, identity: SecureDatabaseIdentity) -> None:
        self.path = path
        self.identity = identity
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        _verify_database_identity(self.path, self.identity)
        uri = readonly_sqlite_uri(self.path)
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        try:
            _verify_database_identity(self.path, self.identity)
        except Exception:
            self.connection.close()
            self.connection = None
            raise
        return self.connection

    def __exit__(self, *_args: object) -> None:
        if self.connection is not None:
            self.connection.close()


def _readonly_connection(path: Path, *, identity: SecureDatabaseIdentity) -> _ReadOnlyConnection:
    return _ReadOnlyConnection(path, identity=identity)


def _verify_database_identity(path: Path, expected: SecureDatabaseIdentity) -> None:
    if capture_database_identity(path, Path(expected.root)) != expected:
        raise InfrastructureError(
            "Database path changed while it was being inspected.",
            "Stop concurrent filesystem changes and retry.",
        )


def _read_safe_log(root: Path, path: Path) -> str:
    if path.is_symlink():
        raise RunCommandError(f"Log {path.name!r} is a symlink; refusing unsafe access.", exit_code=2)
    try:
        resolved = path.resolve(strict=True)
        if root.resolve() not in resolved.parents or not resolved.is_file():
            raise RunCommandError(f"Log {path.name!r} escapes the run directory.", exit_code=2)
        return resolved.read_text(encoding="utf-8", errors="replace")
    except RunCommandError:
        raise
    except OSError as error:
        raise RunCommandError(f"Could not read log {path.name!r}: {error}", exit_code=1) from error


def _checkpoint_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
