"""DAG validation and deterministic wave computation."""
from __future__ import annotations

from stms.domain.errors import DomainError
from stms.domain.models import ApprovedPlan, PlanTask


def task_waves(plan: ApprovedPlan, max_parallel_tasks: int) -> list[list[PlanTask]]:
    if max_parallel_tasks < 1: raise DomainError("max_parallel_tasks must be positive.", "Set it to at least 1.")
    tasks = {task.id: task for task in plan.tasks}
    order = {task.id: index for index, task in enumerate(plan.tasks)}
    unresolved = {task.id: {dependency.task_id for dependency in task.dependencies} for task in plan.tasks}
    completed: set[str] = set(); waves: list[list[PlanTask]] = []
    while unresolved:
        ready = [task_id for task_id, dependencies in unresolved.items() if dependencies <= completed]
        if not ready:
            raise DomainError("The approved plan contains a dependency cycle.", "Remove cyclic task dependencies before approval.")
        ready.sort(key=order.__getitem__)
        for index in range(0, len(ready), max_parallel_tasks):
            wave_ids = ready[index:index + max_parallel_tasks]
            waves.append([tasks[task_id] for task_id in wave_ids])
            completed.update(wave_ids)
            for task_id in wave_ids: unresolved.pop(task_id)
    return waves
