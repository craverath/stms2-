"""Workflow engine facade; LangGraph remains optional and confined to this adapter."""
from __future__ import annotations

from stms.domain.models import ExternalOperation, OperationStatus, WorkflowSnapshot
from stms.domain.errors import InfrastructureError
from stms.domain.ports import WorkflowEngine
from .sqlite_store import SQLiteCheckpointStore


class LocalWorkflowEngine(WorkflowEngine):
    """Checkpoint-before/after facade compatible with a later LangGraph graph adapter.

    The domain never imports LangGraph. Installing the optional dependency may add
    a graph-backed implementation without changing this public workflow contract.
    """
    def __init__(self, store: SQLiteCheckpointStore) -> None:
        self.store = store
        try:
            from langgraph.graph import END, START, StateGraph
            from langgraph.checkpoint.memory import MemorySaver
        except ImportError as error:
            raise InfrastructureError(
                "LangGraph persistence API is unavailable.",
                "Install the supported langgraph dependency; STMS will not silently use an uncheckpointed workflow engine.",
            ) from error
        # LangGraph owns the graph boundary while SQLite remains the durable
        # operational source of truth. The in-memory saver only supplies graph
        # execution semantics; every graph transition is mirrored to SQLite below.
        graph = StateGraph(dict)
        graph.add_node("checkpoint", lambda state: state)
        graph.add_edge(START, "checkpoint")
        graph.add_edge("checkpoint", END)
        self._graph = graph.compile(checkpointer=MemorySaver())

    def _graph_checkpoint(self, snapshot: WorkflowSnapshot) -> None:
        try:
            self._graph.invoke(
                {"snapshot": snapshot.model_dump(mode="json")},
                {"configurable": {"thread_id": snapshot.metadata.run_id}},
            )
        except Exception as error:
            raise InfrastructureError(
                "LangGraph could not checkpoint the workflow state.",
                "Inspect the installed LangGraph version and run resume only after its persistence API is compatible.",
            ) from error

    def checkpoint_before(self, snapshot: WorkflowSnapshot, operation_id: str, kind: str) -> None:
        existing = self.store.operation(snapshot.metadata.run_id, operation_id)
        if existing and existing.status == OperationStatus.CONFIRMED: return
        self._graph_checkpoint(snapshot)
        self.store.save_snapshot(snapshot)
        self.store.record_operation(snapshot.metadata.run_id, ExternalOperation(id=operation_id, kind=kind, status=OperationStatus.PENDING))

    def checkpoint_after(self, snapshot: WorkflowSnapshot, operation_id: str, result_reference: str | None = None) -> None:
        self._graph_checkpoint(snapshot)
        self.store.record_operation(snapshot.metadata.run_id, ExternalOperation(id=operation_id, kind="external", status=OperationStatus.CONFIRMED, result_reference=result_reference))
        self.store.save_snapshot(snapshot)

    def load(self, run_id: str) -> WorkflowSnapshot:
        snapshot = self.store.latest_snapshot(run_id)
        if snapshot is None: raise KeyError(f"Unknown run {run_id}")
        return snapshot
