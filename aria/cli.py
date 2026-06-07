"""
aria/cli.py
────────────
ARIA command-line interface.

Subcommands:
  run                          Launch the TUI dashboard + background scheduler
  improve --tool <name>        Manually trigger improvement for a specific tool
  status                       Print current tool metrics as a table
  rollback --tool <name>       Revert a tool to its last Git-committed version
  run-tool --tool <name>       Execute a tool directly with JSON input
  history                      Show improvement history

Usage:
  python -m aria run
  python -m aria improve --tool search_tool
  python -m aria status
  python -m aria rollback --tool search_tool
  python -m aria run-tool --tool calculator_tool --input '{"expression": "2+2"}'
  python -m aria history
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_run(args: argparse.Namespace) -> None:
    """Launch the interactive terminal menu."""
    from aria.main import bootstrap
    bootstrap()

    from aria.ui.interactive_menu import run_menu
    run_menu()


def cmd_improve(args: argparse.Namespace) -> None:
    """Manually trigger an improvement cycle for a specific tool."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    tool_name = args.tool
    console.print(
        Panel(
            f"[bold cyan]Triggering improvement cycle for:[/] [yellow]{tool_name}[/]",
            title="[bold]ARIA Improvement Engine[/bold]",
            border_style="cyan",
        )
    )

    from aria.main import bootstrap
    bootstrap()

    from aria.core.agent import agent

    with console.status(f"[bold blue]Running improvement cycle for '{tool_name}'..."):
        deployed = agent.run_improvement_cycle(target_tool=tool_name)

    # Print any events that were emitted
    events = agent.get_events(max_items=100)
    for event in events:
        console.print(f"  [dim]→[/dim] {event.message}")

    if deployed:
        console.print(f"\n[bold green]✓ Improvement deployed for '{tool_name}'[/bold green]")
    else:
        console.print(f"\n[bold yellow]✗ No improvement deployed for '{tool_name}'[/bold yellow]")


def cmd_status(args: argparse.Namespace) -> None:
    """Print current tool metrics as a rich table."""
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime

    from aria.main import bootstrap
    bootstrap()

    from aria.metrics.db import get_all_tool_stats
    from aria.tools.registry import registry

    console = Console()

    table = Table(
        title="[bold cyan]ARIA Tool Metrics[/bold cyan]",
        border_style="dim",
        show_lines=True,
    )
    table.add_column("Tool", style="bold white")
    table.add_column("Success Rate", justify="center")
    table.add_column("p90 Latency", justify="center")
    table.add_column("Executions", justify="right")
    table.add_column("Failures", justify="right")
    table.add_column("Last Seen", justify="center")
    table.add_column("Status", justify="center")

    from aria.config import settings
    from aria.metrics.db import get_tool_stats

    for name in registry.names():
        stats = get_tool_stats(name)
        if stats is None:
            table.add_row(
                name, "—", "—", "0", "0", "—",
                "[dim]No data[/dim]"
            )
            continue

        rate = stats.success_rate
        latency = stats.p90_latency
        is_weak = (
            rate < settings.success_rate_threshold
            or latency > settings.latency_threshold_seconds
        )

        rate_color = "green" if rate >= 0.9 else "yellow" if rate >= 0.7 else "red"
        status_str = "[bold red]⚠ WEAK[/bold red]" if is_weak else "[green]✓ Healthy[/green]"

        last_seen = (
            datetime.fromtimestamp(stats.last_seen).strftime("%H:%M:%S")
            if stats.last_seen else "—"
        )

        table.add_row(
            name,
            f"[{rate_color}]{rate:.0%}[/{rate_color}]",
            f"{latency:.3f}s",
            str(stats.total_executions),
            str(stats.failure_count),
            last_seen,
            status_str,
        )

    console.print(table)
    console.print(
        f"\n[dim]Thresholds: success ≥ {settings.success_rate_threshold:.0%}, "
        f"latency ≤ {settings.latency_threshold_seconds}s[/dim]"
    )


def cmd_rollback(args: argparse.Namespace) -> None:
    """Revert a tool to its last committed Git version."""
    from rich.console import Console

    console = Console()
    tool_name = args.tool

    from aria.main import bootstrap
    bootstrap()

    from aria.versioning.git_manager import git_manager
    from aria.tools.registry import registry

    console.print(f"[yellow]Rolling back '{tool_name}' to previous version...[/yellow]")

    success = git_manager.rollback_tool(tool_name)

    if success:
        registry.reload_tool(tool_name)
        console.print(f"[bold green]✓ '{tool_name}' rolled back successfully.[/bold green]")
    else:
        console.print(f"[bold red]✗ Rollback failed. Check Git history manually.[/bold red]")
        sys.exit(1)


def cmd_run_tool(args: argparse.Namespace) -> None:
    """Execute a specific tool with JSON input."""
    from rich.console import Console
    from rich import print_json
    import json

    console = Console()

    from aria.main import bootstrap
    bootstrap()

    from aria.core.agent import agent

    tool_name = args.tool
    try:
        input_data = json.loads(args.input) if args.input else {}
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON input: {exc}[/red]")
        sys.exit(1)

    console.print(f"[cyan]Running tool: [bold]{tool_name}[/bold][/cyan]")
    result = agent.run_tool(tool_name, input_data)

    if result is None:
        console.print(f"[red]Tool '{tool_name}' not found.[/red]")
        sys.exit(1)

    if result.success:
        console.print(f"[green]✓ Success[/green] ({result.latency_seconds:.3f}s)")
        console.print("[dim]Output:[/dim]")
        console.print_json(json.dumps(result.output, default=str))
    else:
        console.print(f"[red]✗ Failed[/red] ({result.latency_seconds:.3f}s)")
        console.print(f"[red]Error:[/red] {result.error}")


def cmd_history(args: argparse.Namespace) -> None:
    """Print improvement history."""
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime

    from aria.main import bootstrap
    bootstrap()

    from aria.metrics.db import get_improvement_history

    console = Console()
    history = get_improvement_history(tool_name=getattr(args, "tool", None), limit=30)

    if not history:
        console.print("[dim]No improvement history found.[/dim]")
        return

    table = Table(
        title="[bold cyan]ARIA Improvement History[/bold cyan]",
        border_style="dim",
        show_lines=True,
    )
    table.add_column("Timestamp", style="dim")
    table.add_column("Tool", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Commit", justify="center")
    table.add_column("Reason")

    for entry in history:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d %H:%M")
        status = entry["status"]
        color = "green" if status == "deployed" else "red" if status == "rejected" else "yellow"
        commit = entry.get("git_commit_hash") or "—"
        reason = (entry.get("reason") or "")[:60]

        table.add_row(
            ts, entry["tool_name"],
            f"[{color}]{status.upper()}[/{color}]",
            f"[dim]{commit}[/dim]",
            reason,
        )

    console.print(table)


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aria",
        description="ARIA — Autonomous Recursive Improvement Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # run
    sub.add_parser("run", help="Launch the TUI dashboard")

    # improve
    improve_p = sub.add_parser("improve", help="Manually trigger improvement for a tool")
    improve_p.add_argument("--tool", required=True, metavar="<tool_name>",
                           help="Name of the tool to improve (e.g. search_tool)")

    # status
    sub.add_parser("status", help="Print current tool metrics")

    # rollback
    rollback_p = sub.add_parser("rollback", help="Revert a tool to its last Git version")
    rollback_p.add_argument("--tool", required=True, metavar="<tool_name>")

    # run-tool
    run_tool_p = sub.add_parser("run-tool", help="Execute a tool directly")
    run_tool_p.add_argument("--tool", required=True, metavar="<tool_name>")
    run_tool_p.add_argument("--input", default="{}", metavar="<json>",
                            help='JSON input dict, e.g. \'{"query": "hello"}\'')

    # history
    history_p = sub.add_parser("history", help="Show improvement history")
    history_p.add_argument("--tool", default=None, metavar="<tool_name>",
                           help="Filter by tool name (optional)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "run": cmd_run,
        "improve": cmd_improve,
        "status": cmd_status,
        "rollback": cmd_rollback,
        "run-tool": cmd_run_tool,
        "history": cmd_history,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)
