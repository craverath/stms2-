"""Interactive STMS command line interface."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess

import typer

import stms
from stms.application.configuration import configuration_example, load_runtime_config
from stms.application.run_admin import (
    RunCommandError,
    abort_run,
    clean_runs,
    find_run,
    inspect_runs,
    next_action,
    read_logs,
)
from stms.composition import compose, compose_preflight
from stms.application.orchestrator import Orchestrator, RunContext
from stms.domain.errors import ConfigurationError, InfrastructureError, StmsError
from stms.domain.models import RunState
from stms.terminal import Terminal

app = typer.Typer(help="STMS local development workflow orchestrator.", no_args_is_help=True)
config_app = typer.Typer(help="Validate and inspect project configuration.")
app.add_typer(config_app, name="config")


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", is_eager=True, help="Show the installed STMS version."),
) -> None:
    if version:
        typer.echo(f"stms {stms.__version__}")
        raise typer.Exit()


@app.command()
def doctor() -> None:
    """Probe readiness without running project tests or starting agents."""
    result = compose_preflight(Path.cwd()).diagnose()
    for item in result.diagnostics:
        typer.echo(f"[{item.status}] {item.name}: {item.detail}")
    if not result.ready:
        raise typer.Exit(code=2)


@config_app.command("validate")
def validate_config() -> None:
    """Validate ./stms.yml and print its deterministic digest."""
    try:
        config = load_runtime_config(Path.cwd())
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"stms.yml is valid; digest {config.digest()}")


@app.command()
def runs() -> None:
    """List persisted runs, newest checkpoint first."""
    records, issues = inspect_runs(Path.cwd())
    if not records and not issues:
        typer.echo("No STMS runs found.")
        return
    for record in records:
        snapshot = record.snapshot
        typer.echo(
            f"{record.run_id} state={snapshot.state.value} phase={snapshot.phase.value} "
            f"checkpoint={record.sequence}@{record.checkpoint_at} progress={record.progress}"
        )
    for issue in issues:
        typer.echo(f"CORRUPT {issue.run_id}: {issue.message}", err=True)
    if issues:
        raise typer.Exit(code=1)


@app.command()
def status(run_id: str | None = typer.Argument(None, help="Run ID; newest checkpoint is the default.")) -> None:
    """Show the latest SQLite checkpoint for one run."""
    try:
        record = find_run(Path.cwd(), run_id)
    except RunCommandError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=error.exit_code) from error
    snapshot = record.snapshot
    typer.echo(f"run: {record.run_id}")
    typer.echo(f"state: {snapshot.state.value}")
    typer.echo(f"phase: {snapshot.phase.value}")
    typer.echo(f"subphase: {snapshot.subphase.value}")
    typer.echo(f"checkpoint: {record.sequence} at {record.checkpoint_at}")
    typer.echo(f"last transition: {snapshot.last_transition or '-'}")
    typer.echo(f"pause: {snapshot.pause_reason or '-'}")
    typer.echo(f"resume state: {snapshot.resume_state.value if snapshot.resume_state else '-'}")
    typer.echo(f"pending operations: {', '.join(record.pending_operations) or 'none'}")
    typer.echo(f"progress: {record.progress}")
    typer.echo(f"next action: {next_action(snapshot)}")


@app.command()
def logs(run_id: str = typer.Argument(..., help="Run ID.")) -> None:
    """Print events and test logs in deterministic order."""
    try:
        sections = read_logs(Path.cwd(), run_id)
    except RunCommandError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=error.exit_code) from error
    if not sections:
        typer.echo(f"No logs found for run {run_id}.")
        return
    for name, content in sections:
        typer.echo(f"== {name} ==")
        typer.echo(content, nl=not content.endswith("\n"))


@app.command()
def abort(
    run_id: str = typer.Argument(..., help="Run ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Mark an inactive run FAILED while preserving all artifacts."""
    try:
        existing = find_run(Path.cwd(), run_id)
    except RunCommandError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=error.exit_code) from error
    if existing.snapshot.state == RunState.FAILED:
        try:
            snapshot, _ = abort_run(Path.cwd(), run_id)
        except RunCommandError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(code=error.exit_code) from error
        typer.echo(f"Run {run_id} is already {snapshot.state.value}.")
        return
    if existing.snapshot.state == RunState.COMPLETED:
        typer.echo(f"Run {run_id!r} is COMPLETED and cannot be aborted.", err=True)
        raise typer.Exit(code=2)
    if not yes and not typer.confirm(f"Abort run {run_id}? Artifacts will be preserved"):
        typer.echo("Abort cancelled.")
        raise typer.Exit(code=1)
    try:
        snapshot, changed = abort_run(Path.cwd(), run_id)
    except RunCommandError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=error.exit_code) from error
    typer.echo(f"Run {run_id} {'aborted' if changed else 'is already'} {snapshot.state.value}.")


@app.command()
def clean(
    dry_run: bool = typer.Option(False, "--dry-run", help="List eligible runs without removing them."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove repository-owned terminal run artifact directories."""
    preview = clean_runs(Path.cwd(), dry_run=True)
    for record in preview.candidates:
        typer.echo(f"{'Would remove' if dry_run else 'Eligible'}: {record.run_id} ({record.snapshot.state.value})")
    for message in preview.ignored:
        typer.echo(f"Ignored: {message}")
    if dry_run or not preview.candidates:
        if preview.errors:
            raise typer.Exit(code=1)
        return
    if not yes and not typer.confirm(f"Remove {len(preview.candidates)} terminal run director{'y' if len(preview.candidates) == 1 else 'ies'}"):
        typer.echo("Clean cancelled.")
        raise typer.Exit(code=1)
    try:
        result = clean_runs(
            Path.cwd(),
            dry_run=False,
            only_run_ids=frozenset(record.run_id for record in preview.candidates),
        )
    except RunCommandError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=error.exit_code) from error
    for run in result.removed:
        typer.echo(f"Removed: {run}")
    for message in result.ignored:
        if message not in preview.ignored:
            typer.echo(f"Ignored: {message}")
    if result.errors:
        for message in result.errors:
            if message not in preview.errors:
                typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1)


@app.command()
def init() -> None:
    """Create a project configuration from the packaged example."""
    config_path = Path.cwd() / "stms.yml"
    try:
        example = configuration_example()
        with config_path.open("x", encoding="utf-8") as config_file:
            config_file.write(example)
    except FileExistsError:
        typer.echo("stms.yml already exists; it was not changed.", err=True)
        raise typer.Exit(code=2)
    except (ConfigurationError, OSError) as error:
        typer.echo(f"Could not create stms.yml: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Created {config_path}")


@app.command()
def update() -> None:
    """Update the installed STMS tool using uv."""
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        typer.echo("Could not update STMS: uv is not installed or is not on PATH.", err=True)
        raise typer.Exit(code=2)

    result = subprocess.run([uv_executable, "tool", "upgrade", "stms"], check=False)
    if result.returncode:
        typer.echo("Could not update STMS; see the uv output above.", err=True)
        raise typer.Exit(code=1)


@app.command()
def uninstall() -> None:
    """Uninstall STMS using uv without changing project files."""
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        typer.echo("Could not uninstall STMS: uv is not installed or is not on PATH.", err=True)
        raise typer.Exit(code=2)

    result = subprocess.run([uv_executable, "tool", "uninstall", "stms"], check=False)
    if result.returncode:
        typer.echo("Could not uninstall STMS; see the uv output above.", err=True)
        raise typer.Exit(code=1)


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
