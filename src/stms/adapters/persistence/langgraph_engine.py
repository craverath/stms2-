"""Workflow engine facade; LangGraph remains optional and confined to this adapter."""
from __future__ import annotations

import sqlite3

from stms.domain.models import ExternalOperation, OperationStatus, WorkflowSnapshot
from stms.domain.errors import InfrastructureError
from stms.domain.ports import WorkflowEngine
from .sqlite_store import SQLiteCheckpointStore


class LocalWorkflowEngine(WorkflowEngine):
    """Checkpoint-before/after facade backed by LangGraph's SQLite checkpointer.

    The domain never imports LangGraph, and importing this module never imports it
    either: the optional `langgraph`/`langgraph-checkpoint-sqlite` dependencies are
    only loaded once an engine is actually constructed, so a missing/incompatible
    install fails here with an actionable error instead of breaking package import.
    """
    def __init__(self, store: SQLiteCheckpointStore) -> None:
        self.store = store
        try:
            from langgraph.graph import END, START, StateGraph
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as error:
            raise InfrastructureError(
                "LangGraph SQLite persistence API is unavailable.",
                "Install the supported langgraph and langgraph-checkpoint-sqlite dependencies; STMS will not silently use an uncheckpointed workflow engine.",
            ) from error
        # SQLite remains the durable operational source of truth for STMS's own
        # snapshots/operations; LangGraph's own SQLite checkpointer gives the graph
        # boundary itself real persistence (not merely in-process memory) so a
        # crash between graph transitions is also recoverable.
        # LangGraph dispatches checkpoint writes on its own worker thread, so the
        # connection must tolerate cross-thread use; a single serialized SQLite
        # connection is safe here because STMS never runs concurrent graph steps.
        self._connection = sqlite3.connect(str(store.database_path.with_name("langgraph.sqlite")), check_same_thread=False)
        checkpointer = SqliteSaver(self._connection)
        graph = StateGraph(dict)
        graph.add_node("checkpoint", lambda state: state)
        graph.add_edge(START, "checkpoint")
        graph.add_edge("checkpoint", END)
        self._graph = graph.compile(checkpointer=checkpointer)

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
