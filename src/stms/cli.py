"""Interactive STMS command line interface."""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from stms.composition import compose
from stms.application.orchestrator import Orchestrator, RunContext
from stms.domain.errors import ConfigurationError, InfrastructureError, StmsError
from stms.domain.models import RunState
from stms.terminal import Terminal

app = typer.Typer(help="STMS local development workflow orchestrator.", no_args_is_help=True)


@app.command()
def start(
    prompt: str | None = typer.Argument(None, help="Development request."),
    file: Path | None = typer.Option(None, "--file", exists=True, dir_okay=False, readable=True, help="UTF-8 request document."),
) -> None:
    """Start a run and stop at each required human approval gate."""
    if (prompt is None) == (file is None):
        typer.echo("Provide exactly one prompt or --file <path>.", err=True)
        raise typer.Exit(code=2)
    try:
        request = file.read_text(encoding="utf-8") if file else prompt or ""
    except UnicodeDecodeError:
        typer.echo("--file must be UTF-8 text.", err=True)
        raise typer.Exit(code=2)
    try:
        code = asyncio.run(_start_interactive(Path.cwd(), request))
    except (ConfigurationError, InfrastructureError, StmsError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2 if isinstance(error, (ConfigurationError, InfrastructureError)) else 1) from error
    raise typer.Exit(code=code)


@app.command()
def resume(run_id: str | None = typer.Argument(None, help="Optional run ID; newest resumable run is the default.")) -> None:
    """Load a persisted run at its last safe checkpoint."""
    try:
        code = asyncio.run(_resume_interactive(Path.cwd(), run_id))
        raise typer.Exit(code=code)
    except StmsError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2 if isinstance(error, (ConfigurationError, InfrastructureError)) else 1) from error


async def _start_interactive(repository: Path, request: str) -> int:
    terminal = Terminal()
    orchestrator = compose(repository, event_renderer=terminal)
    context = orchestrator.start(request)
    message = request
    turns_without_plan = 0
    try:
        while context.workflow.snapshot.state == RunState.INTERVIEWING:
            response = await orchestrator.plan_turn(context, message)
            if response.status == "plan_ready":
                break
            turns_without_plan += 1
            if turns_without_plan >= 10:
                decision = (await terminal.ask("No plan after ten turns. continue, reformulate, or abort?")).strip().lower()
                if decision == "abort":
                    orchestrator.abort(context); return 1
                if decision == "reformulate":
                    message = await terminal.ask("New request:")
                    turns_without_plan = 0
                    continue
            message = await terminal.ask("\n".join(response.questions))
        terminal.markdown(context.workflow.artifacts._path("plan.md").read_text(encoding="utf-8"))
        decision = (await terminal.ask("Approve this plan? (approve/feedback/abort)")).strip().lower()
        if decision == "abort":
            orchestrator.abort(context); return 1
        if decision != "approve":
            orchestrator.feedback(context, await terminal.ask("Plan feedback:")); return 3
        orchestrator.approve_plan(context)
        return await _advance(orchestrator, context, terminal)
    except KeyboardInterrupt:
        # A second Ctrl-C while this confirmation is active is an explicit forced
        # interruption.  The already durable pre-effect checkpoint remains safe.
        try:
            pause = (await terminal.ask("Pause safely and keep this run resumable? (yes/no)")).strip().lower()
        except KeyboardInterrupt:
            return 130
        if pause in {"y", "yes"} and context.workflow.snapshot.state not in {RunState.COMPLETED, RunState.FAILED}:
            context.workflow.pause("keyboard_interrupt")
            return 3
        return 130


async def _resume_interactive(repository: Path, run_id: str | None) -> int:
    terminal = Terminal()
    orchestrator = compose(repository, event_renderer=terminal)
    context = orchestrator.resume(run_id)
    await terminal.write(f"Resumed {context.workflow.snapshot.metadata.run_id}: {context.workflow.snapshot.state}")
    if context.workflow.snapshot.state == RunState.PAUSED:
        await terminal.write("Run remains paused because it requires a human decision (for example, a changed base).")
        return 3
    if context.workflow.snapshot.state == RunState.PLAN_PENDING_APPROVAL:
        decision = (await terminal.ask("Approve restored plan? (approve/feedback/abort)")).strip().lower()
        if decision == "approve":
            orchestrator.approve_plan(context)
        elif decision == "abort":
            orchestrator.abort(context); return 1
        else:
            orchestrator.feedback(context, await terminal.ask("Plan feedback:")); return 3
    if context.workflow.snapshot.state == RunState.INTERVIEWING:
        message = await terminal.ask("Continue planning:")
        await orchestrator.plan_turn(context, message)
        return 3 if context.workflow.snapshot.state != RunState.PLAN_PENDING_APPROVAL else await _resume_interactive(repository, context.workflow.snapshot.metadata.run_id)
    return await _advance(orchestrator, context, terminal)


async def _advance(orchestrator: Orchestrator, context: RunContext, terminal: Terminal) -> int:
    """Drive post-approval states, including bounded test/review correction loops."""
    while True:
        state = context.workflow.snapshot.state
        if state == RunState.IMPLEMENTING:
            await orchestrator.execute_plan(context)
            continue
        if state == RunState.REVIEWING:
            await orchestrator.review(context)
            continue
        if state == RunState.FINAL_APPROVAL:
            final = await terminal.ask("Final decision (approve/adjust/replan/abort):")
            orchestrator.final_decision(context, final, await terminal.ask("Details:"))
            continue
        if state == RunState.INTERVIEWING:
            return 3
        if state == RunState.COMPLETED:
            return 0
        if state == RunState.PAUSED:
            return 3
        return 1
