"""`stasis` CLI entry point (Typer + Rich).

Commands so far (M1):

    stasis fleet
    stasis logs <agent_id> [--follow] [--limit N]

Commands stubbed for later milestones:

    stasis kill <agent_id> --reason "..."        # M4
    stasis grant <agent_id> --symptoms ...       # M5
    stasis revoke <grant_id> --reason "..."      # M5
"""

from __future__ import annotations

import asyncio
import contextlib

import typer
from rich.console import Console
from rich.table import Table

from stasis_agent._version import __version__
from stasis_agent.client import (
    AuthError,
    NotFoundError,
    StasisClient,
    TransportError,
)
from stasis_agent.types import AgentSummary, EventOut, EventType

app = typer.Typer(
    name="stasis",
    help="Stasis CLI — agent supervision via the apoptosis protocol.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    _ = version


# --- shared error handling ----------------------------------------------


def _run(coro: object) -> None:
    """Run an async CLI body and translate SDK exceptions into clean CLI errors."""
    try:
        asyncio.run(coro)  # type: ignore[arg-type]
    except AuthError as exc:
        err_console.print(f"[red]auth error:[/red] {exc}")
        err_console.print(
            "[dim]Set STASIS_API_KEY in your environment or .env file.[/dim]"
        )
        raise typer.Exit(2) from exc
    except NotFoundError as exc:
        err_console.print(f"[red]not found:[/red] {exc}")
        raise typer.Exit(4) from exc
    except TransportError as exc:
        err_console.print(f"[red]cannot reach control plane:[/red] {exc}")
        err_console.print(
            "[dim]Is the server running? `uv run stasis-control-plane`[/dim]"
        )
        raise typer.Exit(5) from exc


# --- fleet ---------------------------------------------------------------


@app.command()
def fleet() -> None:
    """List registered agents and their statuses."""
    _run(_fleet())


async def _fleet() -> None:
    async with StasisClient.from_config() as client:
        agents = await client.list_agents()
    _render_fleet(agents)


def _render_fleet(agents: list[AgentSummary]) -> None:
    if not agents:
        console.print("[dim]no agents registered[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Policy", style="magenta")
    table.add_column("Status", style="yellow")
    table.add_column("Last HB", justify="right")
    table.add_column("Registered", justify="right")
    for a in agents:
        last_hb = a.last_heartbeat_at.strftime("%H:%M:%S") if a.last_heartbeat_at else "-"
        reg = a.registered_at.strftime("%H:%M:%S")
        table.add_row(
            str(a.id),
            a.name,
            a.policy_name,
            a.status.value,
            last_hb,
            reg,
        )
    console.print(table)


# --- logs ----------------------------------------------------------------


@app.command()
def logs(
    agent_id: str = typer.Argument(..., help="Agent UUID. See `stasis fleet`."),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Tail events as they arrive (Ctrl+C to stop)."
    ),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=1000),
    interval: float = typer.Option(
        1.0, "--interval", help="Poll interval in seconds when --follow."
    ),
) -> None:
    """Show events for an agent. Use --follow to tail live."""
    _run(_logs(agent_id, follow=follow, limit=limit, interval=interval))


async def _logs(agent_id: str, *, follow: bool, limit: int, interval: float) -> None:
    async with StasisClient.from_config() as client:
        # Initial page is descending (most recent first); reverse for display
        # so the oldest line in the screen-full is at the top.
        page = await client.list_events(agent_id, limit=limit)
        for ev in reversed(page.events):
            _print_event(ev)

        if not follow:
            return

        last_id = page.last_id or 0
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                await asyncio.sleep(interval)
                page = await client.list_events(agent_id, after_id=last_id, limit=500)
                for ev in page.events:  # already ascending in tail mode
                    _print_event(ev)
                if page.last_id is not None:
                    last_id = page.last_id


def _print_event(ev: EventOut) -> None:
    ts = ev.created_at.strftime("%H:%M:%S")
    match ev.type:
        case EventType.TOOL_CALL:
            tool = ev.payload.get("tool", "?")
            console.print(f"[dim]{ts}[/dim] [cyan]tool[/cyan]      {tool}")
        case EventType.LLM_CALL:
            model = ev.payload.get("model", "?")
            in_tok = ev.payload.get("input_tokens", 0)
            out_tok = ev.payload.get("output_tokens", 0)
            cost = ev.payload.get("cost_usd", 0.0)
            console.print(
                f"[dim]{ts}[/dim] [magenta]llm[/magenta]       "
                f"{model} in={in_tok} out={out_tok} [green]${cost:.4f}[/green]"
            )
        case EventType.LIFECYCLE:
            phase = ev.payload.get("phase", "?")
            extra = " ".join(f"{k}={v}" for k, v in ev.payload.items() if k != "phase")
            console.print(f"[dim]{ts}[/dim] [yellow]lifecycle[/yellow] {phase} [dim]{extra}[/dim]")
        case EventType.HEARTBEAT:
            up = ev.payload.get("uptime_seconds", 0.0)
            console.print(f"[dim]{ts}[/dim] [green]heartbeat[/green] up={up:.1f}s")
        case EventType.SYMPTOM:
            stype = ev.payload.get("symptom_type", "?")
            sev = ev.payload.get("severity", "?")
            color = "red" if sev == "terminal" else "yellow"
            console.print(f"[dim]{ts}[/dim] [{color}]symptom[/{color}]   {stype} ({sev})")
        case _:
            console.print(f"[dim]{ts}[/dim] {ev.type.value} {ev.payload}")


# --- placeholders (later milestones) -------------------------------------


@app.command()
def kill(
    agent_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    """Manually terminate an agent (M4)."""
    _ = reason
    typer.echo(f"kill {agent_id}: not yet implemented (lands in M4)", err=True)
    raise typer.Exit(1)


@app.command()
def grant(
    agent_id: str = typer.Argument(...),
    symptoms: str = typer.Option(..., "--symptoms"),
    duration: str = typer.Option(..., "--duration"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    """Grant apoptosis-proofing for specific symptoms (M5)."""
    _ = (symptoms, duration, reason)
    typer.echo(f"grant {agent_id}: not yet implemented (lands in M5)", err=True)
    raise typer.Exit(1)


@app.command()
def revoke(
    grant_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    """Revoke an active apoptosis-proofing grant (M5)."""
    _ = reason
    typer.echo(f"revoke {grant_id}: not yet implemented (lands in M5)", err=True)
    raise typer.Exit(1)


policies_app = typer.Typer(help="Manage supervision policies (M5).")
app.add_typer(policies_app, name="policies")


@policies_app.command("list")
def policies_list() -> None:
    typer.echo("policies list: not yet implemented (lands in M5)", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()


# Re-export for tests that want to inspect the Typer app.
__all__ = ["_fleet", "_logs", "_print_event", "_render_fleet", "app"]
