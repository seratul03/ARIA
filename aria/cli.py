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
    table.add_column("WDTS", justify="right")
    table.add_column("Upgrades", justify="right")
    table.add_column("Last Seen", justify="center")
    table.add_column("Status", justify="center")

    from aria.config import settings
    from aria.metrics.db import get_tool_stats
    from aria.memory.store import get_improvement_history

    try:
        "\u2713".encode(sys.stdout.encoding or 'ascii')
        check_char = "✓"
        warn_char = "⚠"
    except (UnicodeEncodeError, TypeError):
        check_char = "OK"
        warn_char = "!"

    try:
        from aria.introspection.wdts import get_all_wdts_scores
        wdts_scores = get_all_wdts_scores()
    except ImportError:
        wdts_scores = {}

    for name in registry.names():
        stats = get_tool_stats(name)
        history = get_improvement_history(name, limit=10000)
        upgrades = sum(1 for h in history if h["result"] == "deployed")

        score_dict = wdts_scores.get(name)
        if score_dict:
            wdts_str = f"{score_dict['wdts']:.3f} ([dim]{score_dict['dominant_factor'][:3]}[/dim])"
        else:
            wdts_str = "—"

        if stats is None:
            table.add_row(
                name, "—", "—", "0", "0", "—", wdts_str, str(upgrades), "—",
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
        
        # history, upgrades, and wdts_str are already computed above

        table.add_row(
            name,
            f"[{rate_color}]{rate:.0%}[/{rate_color}]",
            f"{latency:.3f}s",
            str(stats.total_executions),
            str(stats.failure_count),
            f"{fitness:.2f}",
            wdts_str,
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

    from aria.memory.store import get_improvement_history

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
        status = entry["result"]
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
                    host_file_path.write_text(review['generated_code'].replace("\r\n", "\n"), encoding="utf-8")
                    
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
                
                success_imp_id = agent._deploy(review['tool_name'], review['generated_code'], MockReport(), sandbox_result)
                if success_imp_id is not None:
                    update_review_status(review['id'], "approved")
                    cycle_id = review.get('cycle_id')
                    if cycle_id:
                        from aria.knowledge.applications import resolve_rule_applications_by_cycle
                        from aria.config import settings
                        resolve_rule_applications_by_cycle(cycle_id, success_imp_id, "success", str(settings.db_path))
                else:
                    console.print("[red]Deployment failed.[/red]")
        else:
            update_review_status(review['id'], "rejected")
            # For a rejection via CLI review, we record a rejection
            imp_id = agent._record_rejection(review['tool_name'], "Rejected by human review", None)
            cycle_id = review.get('cycle_id')
            if cycle_id:
                from aria.knowledge.applications import resolve_rule_applications_by_cycle
                from aria.config import settings
                resolve_rule_applications_by_cycle(cycle_id, imp_id, "failure", str(settings.db_path))
            console.print(f"[red]Review {review['id']} rejected.[/red]")

def cmd_memory(args: argparse.Namespace) -> None:
    """Show ARIA Memory Dashboard."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from aria.main import bootstrap
    
    # Initialize the database and migrations before querying
    bootstrap()
    
    from aria.memory.dashboard import most_common_failures, most_successful_fixes, worst_tool, fix_reliability_report
    import json
    
    console = Console()
    
    # 1. Failures
    if args.failures:
        fails = most_common_failures()
        table = Table(title="[bold]Most Common Failure Patterns[/bold]", border_style="cyan")
        table.add_column("Signature", justify="left")
        table.add_column("Tools", justify="left")
        table.add_column("Count", justify="right")
        table.add_column("Status", justify="center")
        
        for f in fails:
            status_str = f"[green]resolved[/green]" if f["status"] == "resolved" else f"[yellow]active[/yellow]"
            try:
                tools = ", ".join(json.loads(f["tool_names"]))
            except Exception:
                tools = str(f["tool_names"])
            table.add_row(f["traceback_signature"][:8], tools, str(f["occurrence_count"]), status_str)
            
        console.print(table)
        return
        
    # 2. Fixes
    if args.fixes:
        fixes = most_successful_fixes()
        table = Table(title="[bold]Most Successful Fixes[/bold]", border_style="cyan")
        table.add_column("Tool", justify="left")
        table.add_column("Fix Summary", justify="left")
        table.add_column("Fitness Delta", justify="right")
        table.add_column("Memory Score", justify="right")
        
        for f in fixes:
            f_delta = f['fitness_delta'] if f['fitness_delta'] is not None else 0.0
            m_score = f['memory_score'] if f['memory_score'] is not None else 0.0
            table.add_row(f["tool_name"], f["fix_summary"][:50], f"{f_delta:.4f}", f"{m_score:.4f}")
            
        console.print(table)
        return
        
    # 3. Worst Tool
    if args.worst_tool:
        wt = worst_tool()
        if not wt:
            console.print("[dim]No memory data available to calculate worst tool.[/dim]")
            return
            
        console.print(
            Panel(
                f"[bold red]Tool:[/] {wt['tool_name']}\n"
                f"[bold]Pain Score:[/] {wt['pain_score']:.2f}\n\n"
                f"[dim]Breakdown:[/dim]\n"
                f"  Failure Count: {wt['failure_count']}\n"
                f"  Avg Fix Reliability: {wt['avg_fix_reliability']:.2f}",
                title="[bold]Worst Tool Analytics[/bold]",
                border_style="red",
            )
        )
        return
        
    # 4. Reliability
    if args.reliability:
        rel = fix_reliability_report()
        table = Table(title="[bold]Fix Reliability Report[/bold]", border_style="cyan")
        table.add_column("Tool", justify="left")
        table.add_column("Fix Summary", justify="left")
        table.add_column("Survival %", justify="right")
        table.add_column("Reuses", justify="right")
        
        for r in rel:
            survival_str = f"[green]{r['survival_percentage']:.1f}%[/green]" if r['survival_percentage'] > 80 else f"[yellow]{r['survival_percentage']:.1f}%[/yellow]"
            table.add_row(r["tool_name"], r["fix_summary"][:50], survival_str, f"{r['reuse_success_count']}/{r['reuse_count']}")
            
        console.print(table)
        return

    # 5. Default Summary
    console.print(Panel("[bold cyan]ARIA Memory Dashboard[/bold cyan]\nUse --failures, --fixes, --worst-tool, or --reliability for details."))
    wt = worst_tool()
    if wt:
        console.print(f"[bold red]System Bottleneck:[/] {wt['tool_name']} (Pain Score: {wt['pain_score']:.2f})")
    console.print("")
    
    fails = most_common_failures(limit=3)
    if fails:
        table = Table(title="[bold]Top Active Failures[/bold]", border_style="yellow")
        table.add_column("Signature")
        table.add_column("Tools")
        table.add_column("Count")
        for f in fails:
            if f["status"] == "active":
                try:
                    tools = ", ".join(json.loads(f["tool_names"]))
                except Exception:
                    tools = str(f["tool_names"])
                table.add_row(f["traceback_signature"][:8], tools, str(f["occurrence_count"]))
        console.print(table)
def cmd_why(args: argparse.Namespace) -> None:
    """Answers 'Why am I failing?' with a synthesized Root Cause Report."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.markdown import Markdown
    from aria.main import bootstrap
    
    bootstrap()
    from aria.rootcause.report import generate_root_cause_report
    
    console = Console()
    with console.status("[bold cyan]Synthesizing Root Cause Report...[/bold cyan]"):
        report = generate_root_cause_report(llm_narrative=not args.no_narrative)
        
    console.print(Panel("[bold magenta]Root Cause Report[/bold magenta] — 'Why am I failing?'", expand=False))
    
    # 1. Breakdown
    if report["root_cause_breakdown"]:
        b_table = Table(title="Root Cause Breakdown (by Occurrence)", border_style="cyan")
        b_table.add_column("Category", style="bold")
        b_table.add_column("Share", justify="right")
        for cat, share in report["root_cause_breakdown"].items():
            b_table.add_row(cat, f"{share:.1%}")
        console.print(b_table)
        
    # 2. Clusters & Patterns
    if report["architectural_patterns"]:
        p_table = Table(title="Active Architectural Patterns", border_style="yellow")
        p_table.add_column("Pattern")
        p_table.add_column("Tools")
        for p in report["architectural_patterns"]:
            p_table.add_row(p["pattern_name"], ", ".join(p["affected_tools"]))
        console.print(p_table)
        
    # 3. Hypotheses
    hyp = report["hypotheses"]
    if hyp["proposed"] or hyp["implemented_recent"]:
        h_table = Table(title="Hypotheses Pipeline", border_style="green")
        h_table.add_column("Status")
        h_table.add_column("Proposed Fix")
        h_table.add_column("Targets")
        for h in hyp["proposed"]:
            h_table.add_row("[yellow]Proposed[/yellow]", h["proposed_fix_summary"], ", ".join(h["target_tools"]))
        for h in hyp["implemented_recent"]:
            h_table.add_row("[green]Implemented[/green]", h["proposed_fix_summary"], ", ".join(h["target_tools"]))
        console.print(h_table)
        
    # 4. Durability
    dur = report["fix_durability"]
    total = dur["held"] + dur["rolled_back"]
    if total > 0:
        held_pct = dur["held"] / total
        console.print(f"[bold]Fix Durability:[/bold] {dur['held']}/{total} ({held_pct:.0%}) fixes held up without rollback.")
        
    # 5. Narrative
    if report.get("narrative"):
        console.print(Panel(Markdown(report["narrative"]), title="[bold]ARIA's Synthesis[/bold]", border_style="blue"))


def cmd_rootcause(args: argparse.Namespace) -> None:
    """Show Root Cause Statistics."""
    from rich.console import Console
    from rich.table import Table
    from aria.main import bootstrap
    
    bootstrap()
    from aria.rootcause.statistics import root_cause_breakdown, root_cause_breakdown_by_tool, root_cause_trend
    
    console = Console()
    
    if args.report:
        # Alias for `aria why --no-narrative` or `aria why`
        # We will map it to `cmd_why` basically
        args.no_narrative = False
        cmd_why(args)
        return
        
    if args.trend:
        trend = root_cause_trend(window_days=30)
        if not trend:
            console.print("[dim]No classified patterns found for trend.[/dim]")
            return
            
        # Get all distinct bucket dates
        all_dates = set()
        for cat_data in trend.values():
            for entry in cat_data:
                all_dates.add(entry[0])

def cmd_predictors(args: argparse.Namespace) -> None:
    """Manage ARIA ML Predictors."""
    from rich.console import Console
    from rich.table import Table
    from aria.main import bootstrap
    bootstrap()
    from aria.config import settings
    
    console = Console()
    db_path = str(settings.db_path)
    
    if args.promote is not None:
        import sqlite3
        from aria.predictors.registry import promote_predictor
        from rich.prompt import Confirm
        import json
        
        # Display stats before prompting
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM predictor_registry WHERE id=?", (args.promote,)).fetchone()
        conn.close()
        
        if not row:
            console.print(f"[bold red]Predictor ID {args.promote} not found.[/bold red]")
            return
            
        console.print(f"\n[bold cyan]Promoting Predictor ID: {row['id']}[/bold cyan]")
        console.print(f"Type: {row['predictor_type']}, Version: {row['version']}")
        console.print(f"Test AUC: {row['test_auc']}, Test Accuracy: {row['test_accuracy']}")
        try:
            notes = json.loads(row['notes'])
            console.print(f"Class Balance (1:0): {notes.get('training_samples', 'unknown')} samples, {notes.get('baseline_accuracy', 'unknown'):.2%} baseline")
        except Exception:
            pass
            
        do_promote = Confirm.ask(f"\n[bold yellow]Promote predictor {args.promote} to ACTIVE?[/bold yellow]")
        if do_promote:
            promote_predictor(args.promote, db_path)
            console.print("[bold green]✓ Promoted successfully.[/bold green]")
        else:
            console.print("[yellow]Promotion cancelled.[/yellow]")
        return
        
    if args.rollback is not None:
        from aria.predictors.registry import rollback_predictor
        rollback_predictor(args.rollback, db_path)
        console.print(f"[bold green]✓ {args.rollback} predictor rolled back.[/bold green]")
        return
        
    if args.history is not None:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM predictor_registry WHERE predictor_type=? ORDER BY version DESC", (args.history,)).fetchall()
        conn.close()
        
        table = Table(title=f"Predictor History: {args.history}", border_style="cyan")
        table.add_column("ID")
        table.add_column("Version")
        table.add_column("Status")
        table.add_column("Test AUC")
        table.add_column("Test Acc")
        table.add_column("Created")
        
        for r in rows:
            color = "green" if r["status"] == "active" else "dim"
            table.add_row(str(r["id"]), str(r["version"]), f"[{color}]{r['status']}[/]", f"{r['test_auc']:.3f}", f"{r['test_accuracy']:.3f}", r["trained_at"])
            
        console.print(table)
        return
        
    if args.importances is not None:
        from aria.predictors.registry import get_active_predictor
        import numpy as np
        res = get_active_predictor(args.importances, db_path)
        if not res:
            console.print(f"[red]No active predictor found for type '{args.importances}'[/red]")
            return
            
        model, pid = res
        try:
            rf = model.named_steps["rf"]
            importances = rf.feature_importances_
            
            # Need feature names
            if args.importances == "success":
                from aria.predictors.features import ALL_FEATURES as feats
            elif args.importances == "failure":
                from aria.predictors.features import FAILURE_FEATURES as feats
            else:
                from aria.predictors.features import RISK_FEATURES as feats
                
            indices = np.argsort(importances)[::-1]
            console.print(f"\n[bold]Top Feature Importances ({args.importances} v{pid}):[/bold]")
            for i in indices[:15]:
                console.print(f"  {feats[i]:<35}: {importances[i]:.4f}")
        except Exception as e:
            console.print(f"[red]Could not extract feature importances: {e}[/red]")
        return
        
    # Default / --health
    from aria.predictors.inference import predictor_health_report
    health = predictor_health_report(db_path)
    
    if not health:
        console.print("[dim]No active predictors found. Empty registry.[/dim]")
        return
        
    table = Table(title="ARIA Predictors Health Report", border_style="green")
    table.add_column("Type")
    table.add_column("Version")
    table.add_column("Test AUC", justify="right")
    table.add_column("Act. Acc", justify="right")
    table.add_column("Act. AUC", justify="right")
    table.add_column("Calib. Error", justify="right")
    table.add_column("Alert", style="red bold")
    
    for ptype, metrics in health.items():
        act_acc = f"{metrics['actual_accuracy']:.2f}" if metrics['actual_accuracy'] is not None else "—"
        act_auc = f"{metrics['actual_auc']:.2f}" if metrics['actual_auc'] is not None else "—"
        ece = f"{metrics['calibration_error']:.3f}" if metrics['calibration_error'] is not None else "—"
        alert = metrics['alert'] or "—"
        
        table.add_row(ptype, str(metrics['version']), f"{metrics['test_auc']:.2f}", act_acc, act_auc, ece, alert)
        
    console.print(table)
    if args.health:
        console.print("\n[dim]Note: Drift and calibration alerts are triggered automatically. Use --rollback if necessary.[/dim]")
        dates = sorted(list(all_dates))
        
        table = Table(title="[bold]Root Cause Trend (30 Days)[/bold]", border_style="cyan")
        table.add_column("Category", style="bold")
        for d in dates:
            table.add_column(d, justify="right")
            
        for cat, data in trend.items():
            date_map = {d: val for d, val in data}
            row = [cat]
            for d in dates:
                val = date_map.get(d, 0.0)
                row.append(f"{val:.1%}")
            table.add_row(*row)
            
        console.print(table)
        
    elif args.clusters:
        from aria.metrics.db import get_connection
        
        with get_connection() as conn:
            clusters = conn.execute("SELECT * FROM root_cause_clusters ORDER BY total_occurrences DESC").fetchall()
            
        if not clusters:
            console.print("[dim]No root cause clusters found.[/dim]")
            return
            
        table = Table(title="[bold]Root Cause Clusters[/bold]", border_style="cyan")
        table.add_column("ID", justify="right")
        table.add_column("Category", style="bold")
        table.add_column("Patterns", justify="right")
        table.add_column("Tools")
        table.add_column("Total Occurrences", justify="right")
        
        for c in clusters:
            import json
            try:
                p_count = len(json.loads(c["pattern_ids"]))
                tools = ", ".join(json.loads(c["tool_names"]))
            except Exception:
                p_count = 0
                tools = str(c["tool_names"])
                
            table.add_row(
                str(c["id"]),
                c["root_cause_category"],
                str(p_count),
                tools,
                str(c["total_occurrences"])
            )
            
        console.print(table)
        
    elif args.by_tool:
        breakdown = root_cause_breakdown_by_tool()
        if not breakdown:
            console.print("[dim]No classified patterns found per tool.[/dim]")
            return
            
        # Get all categories
        all_cats = set()
        for tool_data in breakdown.values():
            all_cats.update(tool_data.keys())
        cats = sorted(list(all_cats))
        
        table = Table(title="[bold]Root Cause Breakdown by Tool[/bold]", border_style="cyan")
        table.add_column("Tool", style="bold")
        for c in cats:
            table.add_column(c, justify="right")
            
        for t, cat_data in breakdown.items():
            row = [t]
            for c in cats:
                val = cat_data.get(c, 0.0)
                row.append(f"{val:.1%}")
            table.add_row(*row)
            
        console.print(table)
        
    else:
        # Global stats (default)
        occ_breakdown = root_cause_breakdown(weight_by="occurrence_count")
        pat_breakdown = root_cause_breakdown(weight_by="pattern_count")
        
        if not occ_breakdown:
            console.print("[dim]No classified patterns found.[/dim]")
            return
            
        all_cats = sorted(list(set(occ_breakdown.keys()) | set(pat_breakdown.keys())))
        
        table = Table(title="[bold]Global Root Cause Statistics[/bold]", border_style="cyan")
        table.add_column("Category", style="bold")
        table.add_column("By Occurrence (Pain)", justify="right")
        table.add_column("By Pattern Count (Variety)", justify="right")
        
        for c in all_cats:
            occ_val = occ_breakdown.get(c, 0.0)
            pat_val = pat_breakdown.get(c, 0.0)
            table.add_row(c, f"{occ_val:.1%}", f"{pat_val:.1%}")
            
        console.print(table)
def cmd_knowledge(args: argparse.Namespace) -> None:
    """Interact with the Knowledge Subsystem."""
    from rich.console import Console
    from rich.table import Table
    from aria.main import bootstrap
    
    bootstrap()
    from aria.config import settings
    import sqlite3
    
    console = Console()
    db_path = settings.db_path if hasattr(settings, "db_path") else "aria.db"
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        try:
            # Check if table exists
            conn.execute("SELECT 1 FROM engineering_rules LIMIT 1")
        except sqlite3.OperationalError:
            console.print("[dim]No rules yet. Engineering rules table is empty or missing.[/dim]")
            return

        if getattr(args, "export", False):
            from aria.knowledge.export import export_rules_json
            from pathlib import Path
            output_path = Path("aria") / "knowledge" / "engineering_rules.json"
            console.print("[yellow]Forcing re-export of engineering_rules.json...[/yellow]")
            export_rules_json(db_path, str(output_path))
            console.print("[bold green]✓ Export complete.[/bold green]")
            return
            
        if getattr(args, "rules", False):
            rules = conn.execute("SELECT * FROM engineering_rules WHERE status = 'active' ORDER BY category, confidence DESC").fetchall()
            title = "[bold]Active Engineering Rules[/bold]"
        elif getattr(args, "candidates", False):
            rules = conn.execute("SELECT * FROM engineering_rules WHERE status = 'candidate' ORDER BY category, confidence DESC").fetchall()
            title = "[bold]Candidate Engineering Rules[/bold]"
        elif getattr(args, "deprecated", False):
            rules = conn.execute("SELECT * FROM engineering_rules WHERE status = 'deprecated' ORDER BY category").fetchall()
            title = "[bold]Deprecated Engineering Rules[/bold]"
        else:
            # Summary
            counts = conn.execute("SELECT status, COUNT(*) as count FROM engineering_rules GROUP BY status").fetchall()
            if not counts:
                console.print("[dim]No rules yet.[/dim]")
                return
            
            c_table = Table(title="[bold]Rules by Status[/bold]", border_style="cyan")
            c_table.add_column("Status")
            c_table.add_column("Count", justify="right")
            for row in counts:
                c_table.add_row(row["status"], str(row["count"]))
            console.print(c_table)
            
            console.print("\n[bold]Top Active Rules per Category:[/bold]")
            cats = conn.execute("SELECT DISTINCT category FROM engineering_rules WHERE status='active'").fetchall()
            for cat_row in cats:
                cat = cat_row["category"]
                top_rule = conn.execute("SELECT rule_text FROM engineering_rules WHERE status='active' AND category=? ORDER BY confidence DESC LIMIT 1", (cat,)).fetchone()
                if top_rule:
                    console.print(f"  [cyan]{cat}[/cyan]: {top_rule['rule_text']}")
            return

        if not rules:
            console.print("[dim]No rules found for this filter.[/dim]")
            return
            
        table = Table(title=title, border_style="cyan")
        table.add_column("ID", justify="right")
        table.add_column("Category", style="bold")
        table.add_column("Rule")
        table.add_column("Confidence", justify="right")
        if getattr(args, "deprecated", False):
            table.add_column("Reason")
            
        for r in rules:
            row = [str(r["id"]), r["category"], r["rule_text"], f"{r['confidence']:.2f}"]
            if getattr(args, "deprecated", False):
                row.append(r["deprecation_reason"] or "")
            table.add_row(*row)
            
        console.print(table)


def cmd_reflect(args: argparse.Namespace) -> None:
    """ARIA Self-Reflection and Introspection"""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from aria.main import bootstrap
    
    bootstrap()
    from aria.config import settings
    db_path = str(settings.db_path)
    console = Console()
    
    import sqlite3
    
    if args.report:
        from aria.reflection.report import generate_reflection_report
        with console.status("Generating synthesis report..."):
            report = generate_reflection_report(db_path, llm_narrative=not args.no_narrative)
            
        console.print(Panel("[bold magenta]ARIA Self-Reflection Report[/bold magenta]", expand=False))
        if report.get("narrative"):
            console.print(f"\n[bold]Synthesis[/bold]\n{report['narrative']}\n")
            
        console.print(f"[bold]Active Weaknesses[/bold]: {len(report['active_weaknesses'])}")
        console.print(f"[bold]Recurring Mistakes[/bold]: {len(report['recurring_mistakes'])}")
        console.print(f"[bold]Ineffective Improvements[/bold]: {len(report['ineffective_improvements'])}")
        console.print(f"[bold]Token Waste Findings[/bold]: {len(report['token_waste']['top_findings'])}")
        console.print(f"[bold]Bad Prompts[/bold]: {len(report['bad_prompts'])}")
        
        console.print("\n[bold]Top Priority Proposals[/bold]:")
        for p in report["priority_proposals"]:
            console.print(f"  [{p['priority'].upper()}] {p['title']} (ID: {p['id']})")
            
        return
        
    if args.weaknesses:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM architectural_weaknesses WHERE status='active'").fetchall()
            
            table = Table(title="Active Architectural Weaknesses")
            table.add_column("ID")
            table.add_column("Type")
            table.add_column("Severity")
            table.add_column("Description")
            for r in rows:
                table.add_row(str(r["id"]), r["weakness_type"], r["severity"], r["description"])
            console.print(table)
        return
        
    if args.mistakes:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM recurring_mistakes WHERE status='active'").fetchall()
            
            table = Table(title="Active Recurring Mistakes")
            table.add_column("ID")
            table.add_column("Type")
            table.add_column("Description")
            for r in rows:
                table.add_row(str(r["id"]), r["mistake_type"], r["description"])
            console.print(table)
        return
        
    if args.proposals:
        from aria.reflection.proposals import get_priority_proposals
        props = get_priority_proposals(db_path, limit=20)
        
        table = Table(title="Priority Self-Improvement Proposals")
        table.add_column("ID")
        table.add_column("Priority")
        table.add_column("Type")
        table.add_column("Title")
        
        for p in props:
            color = "red" if p["priority"] == "critical" else "yellow" if p["priority"] == "high" else "white"
            table.add_row(str(p["id"]), f"[{color}]{p['priority']}[/]", p["change_type"], p["title"])
        console.print(table)
        return
        
    if args.proposal:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (args.proposal,)).fetchone()
            if not row:
                console.print(f"[red]Proposal ID {args.proposal} not found.[/red]")
                return
                
            console.print(Panel(f"[bold]{row['title']}[/bold]\n\n"
                                f"Priority: {row['priority']}\n"
                                f"Change Type: {row['change_type']}\n"
                                f"Target Module: {row['target_module']}\n"
                                f"Status: {row['status']}\n\n"
                                f"[bold]Proposal:[/bold]\n{row['proposal_text']}\n\n"
                                f"[bold]Success Metric:[/bold]\n{row['success_metric']}",
                                title=f"Proposal #{row['id']}"))
        return
        
    if args.accept:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE self_improvement_proposals SET status='accepted', accepted_at=CURRENT_TIMESTAMP WHERE id=?", (args.accept,))
            conn.commit()
            console.print(f"[green]Proposal {args.accept} marked as accepted.[/green]")
        return
        
    if args.implemented:
        with sqlite3.connect(db_path) as conn:
            notes = args.notes or ""
            # Set evaluation_at to roughly 20 cycles from now, but since we evaluate based on time we just say +1 day or so.
            # Realistically we evaluate during meta-cycle check.
            conn.execute("UPDATE self_improvement_proposals SET status='implemented', implemented_at=CURRENT_TIMESTAMP, evaluation_at=datetime('now', '+1 day'), implementation_notes=? WHERE id=?", (notes, args.implemented))
            conn.commit()
            console.print(f"[green]Proposal {args.implemented} marked as implemented.[/green]")
        return
        
    if args.outcomes:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM self_improvement_proposals WHERE outcome IS NOT NULL").fetchall()
            
            table = Table(title="Proposal Outcomes")
            table.add_column("ID")
            table.add_column("Title")
            table.add_column("Outcome")
            table.add_column("Notes")
            for r in rows:
                color = "green" if r["outcome"] == "success" else "red" if r["outcome"] == "failure" else "yellow"
                table.add_row(str(r["id"]), r["title"][:40], f"[{color}]{r['outcome']}[/]", str(r["outcome_notes"])[:40])
            console.print(table)
        return
        
    # Default
    console.print("[dim]Use --report, --proposals, --weaknesses, --mistakes, or --proposal <id>[/dim]")




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

    # knowledge
    knowledge_p = sub.add_parser("knowledge", help="Show Knowledge Rules Subsystem")
    knowledge_p.add_argument("--rules", action="store_true", help="Full active rule list")
    knowledge_p.add_argument("--candidates", action="store_true", help="Candidates awaiting promotion")
    knowledge_p.add_argument("--deprecated", action="store_true", help="Deprecated rules + reasons")
    knowledge_p.add_argument("--export", action="store_true", help="Force re-export + git commit")

    # memory
    memory_p = sub.add_parser("memory", help="Show Memory Dashboard")
    memory_p.add_argument("--failures", action="store_true", help="Show most common failure patterns")
    memory_p.add_argument("--fixes", action="store_true", help="Show most successful fixes")
    memory_p.add_argument("--worst-tool", action="store_true", help="Show the worst performing tool based on pain score")
    memory_p.add_argument("--reliability", action="store_true", help="Show fix reliability report")
    # why
    why_p = sub.add_parser("why", help="Generate Root Cause Report with LLM narrative")
    why_p.add_argument("--no-narrative", action="store_true", help="Generate structured data only without LLM narrative")

    # rootcause
    rc_p = sub.add_parser("rootcause", help="Show Root Cause Statistics")
    rc_p.add_argument("--report", action="store_true", help="Alias for 'why' command")
    rc_p.add_argument("--stats", action="store_true", help="Global breakdown")
    rc_p.add_argument("--by-tool", action="store_true", help="Per-tool breakdown")
    rc_p.add_argument("--trend", action="store_true", help="30-day trend")
    rc_p.add_argument("--clusters", action="store_true", help="Show failure pattern clusters")

    # predictors
    pred_p = sub.add_parser("predictors", help="Manage Machine Learning Predictors")
    pred_p.add_argument("--health", action="store_true", help="Show full health report for active predictors")
    pred_p.add_argument("--promote", type=int, help="Promote a candidate predictor by ID to active status")
    pred_p.add_argument("--rollback", type=str, help="Rollback the active predictor for a given type (success, failure, risk)")
    pred_p.add_argument("--history", type=str, help="Show the version history for a given predictor type")
    pred_p.add_argument("--importances", type=str, help="Show feature importances for active predictor of a given type")

    # reflect
    refl_p = sub.add_parser("reflect", help="ARIA Self-Reflection (Phase 6)")
    refl_p.add_argument("--report", action="store_true", help="Full synthesis report")
    refl_p.add_argument("--no-narrative", action="store_true", help="Exclude LLM narrative from report")
    refl_p.add_argument("--weaknesses", action="store_true", help="List architectural weaknesses")
    refl_p.add_argument("--mistakes", action="store_true", help="List recurring mistakes")
    refl_p.add_argument("--ineffective", action="store_true", help="List ineffective improvements")
    refl_p.add_argument("--waste", action="store_true", help="List token waste findings")
    refl_p.add_argument("--prompts", action="store_true", help="List bad prompt findings")
    refl_p.add_argument("--proposals", action="store_true", help="List priority self-improvement proposals")
    refl_p.add_argument("--proposal", type=int, help="Show detail for a specific proposal ID")
    refl_p.add_argument("--accept", type=int, help="Accept a proposal ID")
    refl_p.add_argument("--implemented", type=int, help="Mark a proposal ID as implemented")
    refl_p.add_argument("--notes", type=str, help="Notes for implementation")
    refl_p.add_argument("--outcomes", action="store_true", help="List evaluated proposal outcomes")
    refl_p.add_argument("--trend", nargs=2, metavar=("<metric>", "<n>"), help="Trend for metric over last n cycles")

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
        "knowledge": cmd_knowledge,
        "memory": cmd_memory,
        "why": cmd_why,
        "rootcause": cmd_rootcause,
        "predictors": cmd_predictors,
        "reflect": cmd_reflect,
    }

    handler = dispatch.get(args.command)
    if handler:
        from rich.console import Console
        console = Console()
        try:
            handler(args)
            console.print("\n[bold green]No error detected, Aria is fine.[/bold green]")
        except SystemExit as e:
            if e.code == 0 or e.code is None:
                console.print("\n[bold green]No error detected, Aria is fine.[/bold green]")
            else:
                console.print(f"\n[bold red]error detected: Exited with code {e.code}[/bold red]")
            sys.exit(e.code)
        except Exception as e:
            console.print(f"\n[bold red]error detected: {e}[/bold red]")
            sys.exit(1)
        except KeyboardInterrupt:
            # Handle Ctrl+C as an intentional user abort, not a system error
            console.print("\n[dim]Process interrupted by user.[/dim]")
            sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)
