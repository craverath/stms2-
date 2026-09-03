from pathlib import Path

from stms.adapters.persistence.langgraph_engine import LocalWorkflowEngine
from stms.adapters.persistence.sqlite_store import SQLiteCheckpointStore
from stms.domain.models import RunMetadata, WorkflowSnapshot


def _snapshot(run_id: str) -> WorkflowSnapshot:
    metadata = RunMetadata(run_id=run_id, repository="/repo", branch_base="main", commit_base="abc123", config_digest="digest")
    return WorkflowSnapshot(metadata=metadata)


def test_checkpoint_is_durable_on_disk_not_only_in_process_memory(tmp_path: Path) -> None:
    database = tmp_path / "run" / "checkpoint.sqlite"
    store = SQLiteCheckpointStore(database)
    engine = LocalWorkflowEngine(store)
    snapshot = _snapshot("run-a")

    engine.checkpoint_before(snapshot, "op-1", "harness")
    engine.checkpoint_after(snapshot, "op-1", "artifact.json")

    langgraph_db = database.with_name("langgraph.sqlite")
    assert langgraph_db.is_file()
    assert langgraph_db.stat().st_size > 0

    # A fresh engine instance backed by the same files must observe the prior
    # checkpoint without needing the first engine's in-process objects.
    reopened_store = SQLiteCheckpointStore(database)
    reopened_engine = LocalWorkflowEngine(reopened_store)
    assert reopened_engine.load("run-a").metadata.run_id == "run-a"
    assert reopened_store.operation("run-a", "op-1").status.value == "confirmed"
