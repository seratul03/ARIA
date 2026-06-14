"""
aria/tui/dashboard.py
──────────────────────
ARIA Terminal User Interface built with Textual.

Layout:
  ┌─────────────────────────────────────────────────────────────┐
  │  Header: ARIA name, status, uptime, cycle count             │
  ├──────────────────────┬──────────────────────────────────────┤
  │  Tool Metrics Table  │  Improvement Event Log               │
  │  (left panel)        │  (right panel)                       │
  ├──────────────────────┴──────────────────────────────────────┤
  │  Active Cycle Progress Bar                                   │
  ├─────────────────────────────────────────────────────────────┤
  │  Footer: key bindings                                        │
  └─────────────────────────────────────────────────────────────┘

Key bindings:
  R   — Manually trigger improvement cycle
  I   — Improve specific tool (shows selection prompt)
  H   — Toggle improvement history view
  L   — Toggle full log view
  Q   — Quit ARIA

The TUI polls the agent's event queue every 500ms for live updates.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from threading import Thread
from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Log,
    ProgressBar,
    RichLog,
    Select,
    Static,
)

from aria.core.agent import AgentEvent, EventType


# ── CSS ───────────────────────────────────────────────────────────────────────

ARIA_CSS = """
/* ── App-level ─────────────────────────────────────────────── */
Screen {
    background: #0d1117;
}

/* ── Header ────────────────────────────────────────────────── */
#aria-header {
    background: #161b22;
    color: #58a6ff;
    height: 3;
    padding: 0 2;
    text-style: bold;
    border-bottom: solid #21262d;
}

/* ── Status bar ─────────────────────────────────────────────── */
#status-bar {
    height: 3;
    background: #161b22;
    border-bottom: solid #21262d;
    padding: 0 2;
    color: #8b949e;
}

#status-running {
    color: #3fb950;
    text-style: bold;
}

#status-cycle {
    color: #e3b341;
}

/* ── Main layout ────────────────────────────────────────────── */
#main-layout {
    height: 1fr;
}

/* ── Tool metrics panel ─────────────────────────────────────── */
#metrics-panel {
    width: 45%;
    border: solid #21262d;
    background: #0d1117;
    padding: 1;
}

#metrics-title {
    color: #58a6ff;
    text-style: bold;
    margin-bottom: 1;
}

DataTable {
    background: #0d1117;
    color: #c9d1d9;
}

DataTable > .datatable--header {
    background: #161b22;
    color: #58a6ff;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: #1f6feb33;
}

/* ── Event log panel ────────────────────────────────────────── */
#log-panel {
    width: 1fr;
    border: solid #21262d;
    background: #0d1117;
    padding: 1;
}

#log-title {
    color: #58a6ff;
    text-style: bold;
    margin-bottom: 1;
}

RichLog {
    background: #0d1117;
    color: #c9d1d9;
    scrollbar-color: #21262d;
}

/* ── Progress panel ─────────────────────────────────────────── */
#progress-panel {
    height: 5;
    background: #161b22;
    border: solid #21262d;
    padding: 0 2;
}

#progress-label {
    color: #8b949e;
    height: 1;
}

ProgressBar {
    height: 1;
}

ProgressBar > .bar--bar {
    color: #58a6ff;
    background: #21262d;
}

ProgressBar > .bar--complete {
    color: #3fb950;
}

/* ── Footer ─────────────────────────────────────────────────── */
Footer {
    background: #161b22;
    color: #8b949e;
    border-top: solid #21262d;
}

Footer > .footer--key {
    color: #58a6ff;
    background: #21262d;
}

/* ── Tool selection modal ───────────────────────────────────── */
#tool-select-modal {
    background: #161b22;
    border: double #58a6ff;
    width: 50;
    height: 20;
    padding: 1 2;
}

#modal-title {
    color: #58a6ff;
    text-style: bold;
    margin-bottom: 1;
}
"""


# ── Tool selection modal ──────────────────────────────────────────────────────

class ToolSelectModal(ModalScreen):
    """Modal dialog for selecting a specific tool to improve."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, tool_names: list[str], callback: Callable[[str], None]) -> None:
        super().__init__()
        self._tool_names = tool_names
        self._callback = callback

    def compose(self) -> ComposeResult:
        with Container(id="tool-select-modal"):
            yield Label("Select Tool to Improve", id="modal-title")
            yield ListView(
                *[ListItem(Label(name), id=f"tool-{name}") for name in self._tool_names],
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        tool_name = item_id.replace("tool-", "")
        self.dismiss()
        self._callback(tool_name)


# ── Main Dashboard App ────────────────────────────────────────────────────────

class ARIADashboard(App):
    """ARIA Terminal Dashboard — live monitoring and control."""

    TITLE = "ARIA — Autonomous Recursive Improvement Agent"
    CSS = ARIA_CSS

    BINDINGS = [
        Binding("r", "trigger_cycle", "Run Cycle", show=True),
        Binding("i", "improve_tool", "Improve Tool", show=True),
        Binding("h", "toggle_history", "History", show=True),
        Binding("l", "toggle_log", "Full Log", show=True),
        Binding("q", "quit_aria", "Quit", show=True),
    ]

    _uptime_start: float = 0.0
    _cycle_phase: reactive[str] = reactive("Idle")
    _cycle_progress: reactive[float] = reactive(0.0)
    _status: reactive[str] = reactive("RUNNING")

    def __init__(self) -> None:
        super().__init__()
        self._start_time = time.time()
        self._event_poll_timer: Timer | None = None
        self._metrics_refresh_timer: Timer | None = None
        self._improvement_thread: Thread | None = None

    # ── Layout ─────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # Status bar
        with Horizontal(id="status-bar"):
            yield Label("● RUNNING", id="status-running")
            yield Label("  |  Cycles: 0/5 this hour", id="status-cycle")
            yield Label("  |  Uptime: 00:00:00", id="status-uptime")

        # Main split layout
        with Horizontal(id="main-layout"):
            # Left: metrics table
            with Vertical(id="metrics-panel"):
                yield Label("⚡ TOOL METRICS", id="metrics-title")
                yield DataTable(id="metrics-table", cursor_type="row")

            # Right: event log
            with Vertical(id="log-panel"):
                yield Label("📋 IMPROVEMENT LOG", id="log-title")
                yield RichLog(id="event-log", highlight=True, markup=True, max_lines=200)

        # Progress bar area
        with Vertical(id="progress-panel"):
            yield Label("Ready — press [bold cyan]R[/] to run a cycle", id="progress-label")
            yield ProgressBar(id="cycle-progress", total=100, show_eta=False)

        yield Footer()

    def on_mount(self) -> None:
        """Set up table columns and start polling timers."""
        table = self.query_one("#metrics-table", DataTable)
        table.add_columns("Tool", "Success", "p90 Latency", "Executions", "Status")

        # Populate initial rows for all registered tools
        self._refresh_metrics_table()

        # Poll agent events every 500ms
        self._event_poll_timer = self.set_interval(0.5, self._poll_events)

        # Refresh metrics table every 10s
        self._metrics_refresh_timer = self.set_interval(10.0, self._refresh_metrics_table)

        # Refresh uptime every second
        self.set_interval(1.0, self._update_uptime)

        # Welcome log message
        log = self.query_one("#event-log", RichLog)
        log.write(
            f"[bold cyan]ARIA v1.0.0[/] started at "
            f"[yellow]{datetime.now().strftime('%H:%M:%S')}[/]\n"
            "[dim]Press [bold]R[/bold] to run an improvement cycle, "
            "[bold]I[/bold] to improve a specific tool.[/dim]"
        )

    # ── Timers & polling ────────────────────────────────────────────────────────

    def _poll_events(self) -> None:
        """Drain the agent's event queue and update the log."""
        from aria.core.agent import agent

        events = agent.get_events(max_items=20)
        if not events:
            return

        log = self.query_one("#event-log", RichLog)

        for event in events:
            ts = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")
            formatted = self._format_event(ts, event)
            log.write(formatted)

            # Update progress bar based on event type
            self._handle_progress(event)

        # Update cycle count
        cycle_label = self.query_one("#status-cycle", Label)
        cycle_label.update(
            f"  |  Cycles: {agent.cycles_this_hour}/{agent.max_cycles_per_hour} this hour"
        )

    def _refresh_metrics_table(self) -> None:
        """Refresh the tool metrics DataTable from SQLite."""
        from aria.introspection.engine import IntrospectionEngine

        engine = IntrospectionEngine()
        health = engine.get_health_summary()

        table = self.query_one("#metrics-table", DataTable)
        table.clear()

        # Also add tools with no data yet
        from aria.tools.registry import registry
        all_names = registry.names()

        for name in all_names:
            data = health.get(name)
            if data:
                rate = data["success_rate"]
                latency = data["p90_latency"]
                execs = data["total_executions"]
                is_weak = data["is_weak"]

                rate_str = f"{rate:.0%}"
                latency_str = f"{latency:.2f}s"
                execs_str = str(execs)

                if is_weak:
                    status = "[bold red]⚠ WEAK[/]"
                    rate_str = f"[red]{rate_str}[/]"
                elif rate >= 0.9:
                    status = "[green]✓ Healthy[/]"
                    rate_str = f"[green]{rate_str}[/]"
                else:
                    status = "[yellow]~ Fair[/]"
                    rate_str = f"[yellow]{rate_str}[/]"
            else:
                rate_str = "[dim]N/A[/]"
                latency_str = "[dim]N/A[/]"
                execs_str = "[dim]0[/]"
                status = "[dim]No data[/]"

            table.add_row(name, rate_str, latency_str, execs_str, status)

    def _update_uptime(self) -> None:
        elapsed = time.time() - self._start_time
        td = timedelta(seconds=int(elapsed))
        uptime_label = self.query_one("#status-uptime", Label)
        uptime_label.update(f"  |  Uptime: {str(td)}")

    def _handle_progress(self, event: AgentEvent) -> None:
        """Update the progress bar based on the current cycle phase."""
        progress_map = {
            EventType.CYCLE_STARTED:      (5,  "🔍 Analyzing metrics..."),
            EventType.WEAKNESS_FOUND:     (20, f"⚠ Weakness found: {event.data.get('tool', '')}"),
            EventType.GENERATING:         (40, "🤖 Generating improvement via Groq LLM..."),
            EventType.STATIC_VALIDATION:  (60, "🔒 Running static security analysis..."),
            EventType.SANDBOX_VALIDATION: (80, "🐳 Running Docker sandbox tests..."),
            EventType.DEPLOYED:           (100, f"✅ Deployed: {event.data.get('tool', '')}"),
            EventType.REJECTED:           (100, f"❌ Rejected: {event.data.get('tool', '')}"),
            EventType.ROLLED_BACK:        (100, "↩ Rolled back to previous version"),
            EventType.CYCLE_COMPLETE:     (100, "✓ Cycle complete"),
            EventType.CYCLE_SKIPPED:      (0,  "— No weak tools detected"),
        }

        if event.type in progress_map:
            pct, label_text = progress_map[event.type]
            progress_bar = self.query_one("#cycle-progress", ProgressBar)
            progress_bar.progress = pct

            progress_label = self.query_one("#progress-label", Label)
            progress_label.update(label_text)

            # Reset after completion
            if pct == 100:
                self.set_timer(3.0, self._reset_progress)

    def _reset_progress(self) -> None:
        progress_bar = self.query_one("#cycle-progress", ProgressBar)
        progress_bar.progress = 0
        progress_label = self.query_one("#progress-label", Label)
        progress_label.update("Ready — press [bold cyan]R[/] to run a cycle")

    # ── Event formatting ────────────────────────────────────────────────────────

    def _format_event(self, ts: str, event: AgentEvent) -> str:
        colors = {
            EventType.CYCLE_STARTED:      "cyan",
            EventType.CYCLE_SKIPPED:      "dim",
            EventType.WEAKNESS_FOUND:     "yellow",
            EventType.GENERATING:         "blue",
            EventType.STATIC_VALIDATION:  "magenta",
            EventType.SANDBOX_VALIDATION: "cyan",
            EventType.DEPLOYED:           "green",
            EventType.REJECTED:           "red",
            EventType.ROLLED_BACK:        "yellow",
            EventType.CYCLE_COMPLETE:     "green",
            EventType.ERROR:              "bold red",
            EventType.TOOL_EXECUTED:      "dim",
        }
        color = colors.get(event.type, "white")
        return f"[dim]{ts}[/dim] [{color}]{event.message}[/{color}]"

    # ── Actions (key bindings) ──────────────────────────────────────────────────

    def action_trigger_cycle(self) -> None:
        """R — manually trigger an improvement cycle."""
        self._run_cycle_in_background(target_tool=None)

    def action_improve_tool(self) -> None:
        """I — pick a specific tool to improve."""
        from aria.tools.registry import registry
        tool_names = registry.names()

        def on_select(tool_name: str) -> None:
            self._run_cycle_in_background(target_tool=tool_name)

        self.push_screen(ToolSelectModal(tool_names, on_select))

    def action_toggle_history(self) -> None:
        """H — show improvement history in the log panel."""
        from aria.memory.store import get_improvement_history
        from datetime import datetime

        history = get_improvement_history(limit=20)
        log = self.query_one("#event-log", RichLog)
        log.write("\n[bold cyan]── Improvement History ──[/bold cyan]")

        if not history:
            log.write("[dim]No improvement history yet.[/dim]")
            return

        for entry in history:
            ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d %H:%M")
            status = entry["result"]
            color = "green" if status == "deployed" else "red" if status == "rejected" else "yellow"
            log.write(
                f"[dim]{ts}[/dim] [{color}]{status.upper()}[/{color}] "
                f"[bold]{entry['tool_name']}[/bold] — {entry.get('reason', '')[:80]}"
            )

    def action_toggle_log(self) -> None:
        """L — scroll to bottom of log."""
        log = self.query_one("#event-log", RichLog)
        log.scroll_end(animate=True)

    def action_quit_aria(self) -> None:
        """Q — graceful shutdown."""
        from aria.core.scheduler import scheduler
        scheduler.stop()
        self.exit()

    # ── Background cycle execution ──────────────────────────────────────────────

    def _run_cycle_in_background(self, target_tool: str | None) -> None:
        """Run improvement cycle in a background thread so TUI stays responsive."""
        if self._improvement_thread and self._improvement_thread.is_alive():
            log = self.query_one("#event-log", RichLog)
            log.write("[yellow]A cycle is already in progress. Please wait.[/yellow]")
            return

        def _run() -> None:
            from aria.core.agent import agent
            agent.run_improvement_cycle(target_tool=target_tool)

        self._improvement_thread = Thread(
            target=_run,
            name="aria-manual-cycle",
            daemon=True,
        )
        self._improvement_thread.start()
