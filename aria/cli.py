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

# ── Windows UTF-8 fix ──────────────────────────────────────────────────────────
# Rich uses unicode symbols (✓, ✗, ─, ≥, …) that cp1252 (the default Windows
# console encoding) cannot represent.  Force UTF-8 so we never get a
# UnicodeEncodeError from the Rich renderer.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        # reconfigure() was added in Python 3.7; should always be present here
        pass


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

    try:
        "\u2192".encode(sys.stdout.encoding or 'ascii')
        arrow = "\u2192"
    except (UnicodeEncodeError, TypeError):
        arrow = "->"

    # Print any events that were emitted
    events = agent.get_events(max_items=100)
    pending_review = False
    for event in events:
        msg = event.message
        if arrow == "->":
            msg = msg.encode('ascii', 'replace').decode('ascii')
        console.print(f"  [dim]{arrow}[/dim] {msg}")
        if event.data.get("status") == "pending_review":
            pending_review = True

    if deployed:
        console.print(f"\n[bold green]✓ Improvement deployed for '{tool_name}'[/bold green]")
    elif pending_review:
        from aria.metrics.db import get_pending_reviews, update_review_status, get_tool_stats
        from rich.prompt import Confirm
        import json
        
        pending = get_pending_reviews()
        review = next((r for r in pending if r['tool_name'] == tool_name and r['status'] == 'pending'), None)
        if review:
            do_deploy = Confirm.ask(f"\n[bold yellow]Deploy these changes? (press y for approve or n for deny)[/bold yellow]")
            if do_deploy:
                combat_report = json.loads(review['combat_report'])
                c = combat_report["clone"]
                stats = get_tool_stats(tool_name)
                
                class MockReport:
                    success_rate = stats.success_rate if stats else 0.0
                    p90_latency = stats.p90_latency if stats else 0.0
                    
                sandbox_result = {
                    "tests_passed": c.get("tests_passed", 0),
                    "tests_total": c.get("tests_total", 0)
                }
                
                success = agent._deploy(tool_name, review['generated_code'], MockReport(), sandbox_result)
                if success:
                    update_review_status(review['id'], "approved")
                    console.print(f"\n[bold green]✓ Improvement deployed for '{tool_name}'[/bold green]")
                else:
                    console.print(f"\n[bold red]✗ Deployment failed.[/bold red]")
            else:
                update_review_status(review['id'], "rejected")
                console.print(f"\n[bold yellow]✗ Improvement rejected by user.[/bold yellow]")
        else:
            console.print(f"\n[bold yellow]✗ No improvement deployed for '{tool_name}'[/bold yellow]")
    else:
        console.print(f"\n[bold yellow]✗ No improvement deployed for '{tool_name}'[/bold yellow]")


def cmd_synthesize(args: argparse.Namespace) -> None:
    """Manually trigger tool synthesis."""
    from rich.console import Console
    from rich.panel import Panel
    import sys

    console = Console()
    tool_name = args.tool
    spec = args.spec

    console.print(
        Panel(
            f"[bold cyan]Triggering Tool Synthesis for:[/] [yellow]{tool_name}[/]\n"
            f"[dim]Specification: {spec}[/dim]",
            title="[bold]ARIA Tool Synthesis Engine[/bold]",
            border_style="cyan",
        )
    )

    from aria.main import bootstrap
    bootstrap()

    from aria.core.agent import agent

    with console.status(f"[bold blue]Synthesizing '{tool_name}'... (This may take a few minutes)"):
        deployed = agent.synthesize_new_tool(tool_name, spec)

    try:
        "\u2192".encode(sys.stdout.encoding or 'ascii')
        arrow = "\u2192"
    except (UnicodeEncodeError, TypeError):
        arrow = "->"

    # Print any events that were emitted
    events = agent.get_events(max_items=100)
    for event in events:
        msg = event.message
        if arrow == "->":
            msg = msg.encode('ascii', 'replace').decode('ascii')
        console.print(f"  [dim]{arrow}[/dim] {msg}")

    if deployed:
        console.print(f"\n[bold green]✓ '{tool_name}' synthesized and deployed successfully![/bold green]")
    else:
        console.print(f"\n[bold red]✗ Tool synthesis failed.[/bold red]")


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
    table.add_column("Fitness", justify="right")
    table.add_column("Upgrades", justify="right")
    table.add_column("Last Seen", justify="center")
    table.add_column("Status", justify="center")

    from aria.config import settings
    from aria.metrics.db import get_tool_stats, get_improvement_history

    try:
        "\u2713".encode(sys.stdout.encoding or 'ascii')
        check_char = "✓"
        warn_char = "⚠"
    except (UnicodeEncodeError, TypeError):
        check_char = "OK"
        warn_char = "!"

    for name in registry.names():
        stats = get_tool_stats(name)
        if stats is None:
            table.add_row(
                name, "—", "—", "0", "0", "—", "0", "—",
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
        status_str = f"[bold red]{warn_char} WEAK[/bold red]" if is_weak else f"[green]{check_char} Healthy[/green]"

        last_seen = (
            datetime.fromtimestamp(stats.last_seen).strftime("%H:%M:%S")
            if stats.last_seen else "—"
        )
        
        fitness = (
            settings.weight_pass_rate * stats.success_rate
            - settings.weight_latency * stats.avg_latency
            - settings.weight_memory * stats.avg_memory_mb
            - settings.weight_tokens * stats.avg_tokens_used
        )
        
        history = get_improvement_history(name, limit=10000)
        upgrades = sum(1 for h in history if h["status"] == "deployed")

        table.add_row(
            name,
            f"[{rate_color}]{rate:.0%}[/{rate_color}]",
            f"{latency:.3f}s",
            str(stats.total_executions),
            str(stats.failure_count),
            f"{fitness:.2f}",
            str(upgrades),
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


def cmd_meta_rollback(args: argparse.Namespace) -> None:
    """Revert the entire repository to a specific tag."""
    from rich.console import Console
    import sys

    console = Console()
    tag_name = args.to

    from aria.main import bootstrap
    bootstrap()

    from aria.versioning.git_manager import git_manager

    console.print(f"[yellow]Executing meta-rollback to tag '{tag_name}'...[/yellow]")

    success = git_manager.rollback_to_tag(tag_name)

    if success:
        console.print(f"[bold green]✓ Meta-rollback to '{tag_name}' completed successfully.[/bold green]")
        console.print("[dim]Note: Run `python -m aria run` to start the dashboard with the restored state.[/dim]")
    else:
        console.print(f"[bold red]✗ Meta-rollback failed. Tag might not exist.[/bold red]")
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


def cmd_traces(args: argparse.Namespace) -> None:
    """Query and display ARIA cycle traces."""
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime
    import json

    from aria.main import bootstrap
    bootstrap()

    from aria.metrics.db import query_cycle_traces

    console = Console()
    
    traces = query_cycle_traces(
        limit=args.last,
        component=args.component,
        outcome=args.outcome,
        tool=args.tool,
    )

    if not traces:
        console.print("[dim]No traces found matching criteria.[/dim]")
        return

    table = Table(
        title="[bold cyan]ARIA Improvement Cycle Traces[/bold cyan]",
        border_style="dim",
        show_lines=True,
    )
    table.add_column("Timestamp", style="dim")
    table.add_column("Cycle ID", style="bold")
    table.add_column("Component", style="dim")
    table.add_column("Outcome", justify="center")
    table.add_column("Trigger (Tool)")
    table.add_column("Duration")
    table.add_column("Candidates")

    for t in traces:
        ts = datetime.fromtimestamp(t["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        cycle_id = t["cycle_id"][:8] + "..." if t["cycle_id"] else "—"
        outcome = t["cycle_outcome"]
        color = "green" if outcome == "IMPROVED" else "yellow" if outcome == "NO_IMPROVEMENT" else "red"
        
        trigger = (t["trigger"] or "")[:50]
        duration = f"{t['duration_seconds']:.1f}s"
        
        try:
            rej_count = len(json.loads(t['candidates_rejected'] or '{}'))
        except Exception:
            rej_count = 0
            
        candidates = f"Gen: {t['candidates_generated']} | Rej: {rej_count} | Dep: {t['candidates_deployed']}"
        
        table.add_row(
            ts, cycle_id, t["component"],
            f"[{color}]{outcome}[/{color}]",
            trigger, duration, candidates
        )

    console.print(table)


def cmd_review(args: argparse.Namespace) -> None:
    """Review and approve pending deployments."""
    from rich.console import Console
    from rich.prompt import Confirm
    from rich.table import Table
    from datetime import datetime
    import json

    from aria.main import bootstrap
    bootstrap()

    from aria.metrics.db import get_pending_reviews, update_review_status, get_tool_stats
    from aria.core.agent import agent

    console = Console()
    pending = get_pending_reviews()

    if not pending:
        console.print("[bold green]No pending deployments in the review queue.[/bold green]")
        return

    for review in pending:
        console.print(f"\n[bold cyan]── Pending Review ID: {review['id']} ──[/bold cyan]")
        console.print(f"Tool: [yellow]{review['tool_name']}[/yellow]")
        console.print(f"Session: {review['session_id']}")
        
        combat_report = json.loads(review['combat_report'])
        
        table = Table(show_header=True, header_style="bold")
        table.add_column("")
        table.add_column("Current ARIA", justify="center")
        table.add_column("Clone", justify="center")
        table.add_column("Delta", justify="right")
        
        b = combat_report["baseline"]
        c = combat_report["clone"]
        
        def fmt_delta(delta: float, is_ms=False) -> str:
            if abs(delta) < 0.001:
                prefix = ""
                is_positive = False
            else:
                prefix = "+" if delta > 0 else ""
                is_positive = delta > 0
                
            if is_ms:
                val = f"{prefix}{delta:.0f}ms"
            else:
                val = f"{prefix}{delta:.5f}"
                
            if abs(delta) < 0.001:
                return f"{val} ➖"
            elif is_positive and not is_ms:
                return f"[green]{val}[/green] ✅"
            elif not is_positive and is_ms:
                return f"[green]{val}[/green] ✅"
            else:
                return f"[red]{val}[/red] ❌"
                
        c_delta = c["correctness"] - b["correctness"]
        r_delta = c["robustness"] - b["robustness"]
        l_delta = (c["latency_p90"] - b["latency_p90"]) * 1000
        s_delta = c["overall_score"] - b["overall_score"]
        
        table.add_row("Correctness:", f"{b['correctness']:.5f}", f"{c['correctness']:.5f}", fmt_delta(c_delta))
        table.add_row("Latency P90 (ms):", f"{b['latency_p90']*1000:.0f}", f"{c['latency_p90']*1000:.0f}", fmt_delta(l_delta, is_ms=True))
        table.add_row("Robustness:", f"{b['robustness']:.5f}", f"{c['robustness']:.5f}", fmt_delta(r_delta))
        
        sg = combat_report.get("safety_gate", "PASS")
        sg_fmt = "✅" if sg == "PASS" else "❌"
        table.add_row("Safety Gate:", sg, sg, sg_fmt)
        
        table.add_row("Overall Score:", f"{b['overall_score']:.5f}", f"{c['overall_score']:.5f}", fmt_delta(s_delta))
        
        console.print(table)
        
        do_deploy = Confirm.ask(f"\n[bold yellow]Deploy these changes to {review['tool_name']}?[/bold yellow]")
        if do_deploy:
            if review['tool_name'].startswith("aria/") or review['tool_name'].endswith(".py"):
                # Meta-improvement deployment
                import shutil
                from pathlib import Path
                from aria.versioning.git_manager import git_manager
                import time
                
                host_file_path = Path(review['tool_name'])
                host_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # We need to write the generated_code from the review to the host_file_path
                try:
                    git_manager.tag_commit(f"pre_meta_deployment_{int(time.time())}")
                    host_file_path.write_text(review['generated_code'], encoding="utf-8")
                    
                    commit_msg = f"Meta-improvement: Human Approved"
                    commit_hash = git_manager.commit_file(host_file_path, commit_msg)
                    if commit_hash:
                        git_manager.tag_commit(f"post_meta_deployment_{int(time.time())}", commit_hash)
                        
                    update_review_status(review['id'], "approved")
                    console.print(f"[bold green]✓ Meta-improvement deployed for '{review['tool_name']}'[/bold green]")
                except Exception as e:
                    console.print(f"[red]Deployment failed: {e}[/red]")
            else:
                # Tool deployment
                stats = get_tool_stats(review['tool_name'])
                class MockReport:
                    success_rate = stats.success_rate if stats else 0.0
                    p90_latency = stats.p90_latency if stats else 0.0
                    
                sandbox_result = {
                    "tests_passed": c.get("tests_passed", 0),
                    "tests_total": c.get("tests_total", 0)
                }
                
                success = agent._deploy(review['tool_name'], review['generated_code'], MockReport(), sandbox_result)
                if success:
                    update_review_status(review['id'], "approved")
                else:
                    console.print("[red]Deployment failed.[/red]")
        else:
            update_review_status(review['id'], "rejected")
            console.print(f"[red]Review {review['id']} rejected.[/red]")

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

    # synthesize
    synthesize_p = sub.add_parser("synthesize", help="Synthesize a new tool from scratch")
    synthesize_p.add_argument("--tool", required=True, metavar="<tool_name>")
    synthesize_p.add_argument("--spec", required=True, metavar="<specification>", help="Description of what the tool should do")

    # status
    sub.add_parser("status", help="Print current tool metrics")

    # rollback
    rollback_p = sub.add_parser("rollback", help="Revert a tool to its last Git version")
    rollback_p.add_argument("--tool", required=True, metavar="<tool_name>")

    # meta-rollback
    meta_rollback_p = sub.add_parser("meta-rollback", help="Revert the entire state to a specific meta tag")
    meta_rollback_p.add_argument("--to", required=True, metavar="<tag_name>", help="Tag to rollback to (e.g. pre_meta_improvement_123456)")

    # run-tool
    run_tool_p = sub.add_parser("run-tool", help="Execute a tool directly")
    run_tool_p.add_argument("--tool", required=True, metavar="<tool_name>")
    run_tool_p.add_argument("--input", default="{}", metavar="<json>",
                            help='JSON input dict, e.g. \'{"query": "hello"}\'')

    # history
    history_p = sub.add_parser("history", help="Show improvement history")
    history_p.add_argument("--tool", default=None, metavar="<tool_name>",
                           help="Filter by tool name (optional)")

    # traces
    traces_p = sub.add_parser("traces", help="Query trace history")
    traces_p.add_argument("--last", type=int, default=10, metavar="N", help="Limit to N recent traces")
    traces_p.add_argument("--component", default=None, help="Filter by component")
    traces_p.add_argument("--outcome", default=None, help="Filter by outcome (e.g. NO_IMPROVEMENT)")
    traces_p.add_argument("--tool", default=None, help="Filter by tool name in trigger")

    # review
    sub.add_parser("review", help="Review and approve pending deployments")

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
        "synthesize": cmd_synthesize,
        "status": cmd_status,
        "rollback": cmd_rollback,
        "meta-rollback": cmd_meta_rollback,
        "run-tool": cmd_run_tool,
        "history": cmd_history,
        "traces": cmd_traces,
        "review": cmd_review,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)
