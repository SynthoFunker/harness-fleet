"""
HMUX Fleet Dashboard & Terminal UI
Rich-powered live visualization of all running and historical harnesses.
"""

import os
import time
import psutil
from datetime import datetime
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.live import Live

from hmux.state import StateManager, FleetSession, ExternalHarness
from hmux.registry import HarnessRegistry

console = Console()

def format_status(status: str) -> Text:
    if status == "RUNNING":
        return Text("● RUNNING", style="bold green")
    elif status == "COMPLETED":
        return Text("✓ COMPLETED", style="bold cyan")
    elif status == "STOPPED":
        return Text("■ STOPPED", style="dim yellow")
    elif status == "FAILED":
        return Text("✖ FAILED", style="bold red")
    return Text(status, style="white")

def format_resource(pid: Optional[int]) -> str:
    if not pid:
        return "-"
    try:
        p = psutil.Process(pid)
        cpu = p.cpu_percent(interval=0.0)
        mem = p.memory_info().rss / (1024 * 1024)
        return f"{cpu:.1f}% | {mem:.0f}MB"
    except Exception:
        return "-"

def extract_model_from_cmdline(cmdline: str) -> Optional[str]:
    parts = cmdline.split()
    for i, part in enumerate(parts):
        if part in ("-m", "--model") and i + 1 < len(parts):
            return parts[i + 1]
    return None

def build_fleet_table(sessions: List[FleetSession], reg: Optional[HarnessRegistry] = None) -> Table:
    if reg is None:
        reg = HarnessRegistry()

    table = Table(title="🤖 HMUX Harness Fleet Status", header_style="bold magenta", expand=True)
    table.add_column("Session ID", style="cyan", no_wrap=True)
    table.add_column("Harness", style="bold yellow")
    table.add_column("Model", style="bold blue")
    table.add_column("Task / Branch", style="green")
    table.add_column("Status", justify="center")
    table.add_column("PID", justify="right", style="dim")
    table.add_column("CPU / Mem", justify="right")
    table.add_column("Started", style="blue")

    if not sessions:
        table.add_row("-", "-", "No active sessions", "-", "-", "-", "-", "-")
        return table

    for s in sessions:
        branch_str = s.branch or (os.path.basename(s.worktree_path) if s.worktree_path else "main")
        start_short = s.start_time.split("T")[-1][:8] if s.start_time else "-"
        resolved_model = s.model or reg.resolve_model(s.harness)
        table.add_row(
            s.session_id,
            s.harness,
            resolved_model,
            f"{s.task_name} ({branch_str})",
            format_status(s.status),
            str(s.pid or "-"),
            format_resource(s.pid),
            start_short
        )

    return table

def build_external_table(externals: List[ExternalHarness], reg: Optional[HarnessRegistry] = None) -> Table:
    if reg is None:
        reg = HarnessRegistry()

    table = Table(title="🌐 Ambient / External Harnesses (Detected in OS)", header_style="bold blue", expand=True)
    table.add_column("Harness", style="bold yellow")
    table.add_column("Model", style="bold blue")
    table.add_column("PID", justify="right", style="cyan")
    table.add_column("Working Directory (CWD)", style="green")
    table.add_column("Resource Usage", justify="right")
    table.add_column("Command Preview", style="dim", no_wrap=False)

    if not externals:
        table.add_row("-", "-", "-", "No unmanaged harness processes running", "-", "-")
        return table

    for e in externals:
        cmd_prev = e.cmdline[:60] + "..." if len(e.cmdline) > 60 else e.cmdline
        model = extract_model_from_cmdline(e.cmdline) or reg.resolve_model(e.harness)
        table.add_row(
            e.harness,
            model,
            str(e.pid),
            e.cwd,
            f"{e.cpu_percent:.1f}% | {e.memory_mb:.0f}MB",
            cmd_prev
        )
    return table

def print_status_table() -> None:
    """Print one-shot status table to terminal."""
    mgr = StateManager()
    sessions = mgr.list_all()
    reg = HarnessRegistry()
    installed = [h.key for h in reg.list_installed()]
    externals = mgr.discover_external_harnesses(reg)

    console.print(f"[bold cyan]HMUX[/bold cyan] - Agent Harness Fleet Manager | Installed harnesses: [bold yellow]{', '.join(installed)}[/bold yellow]\n")
    console.print(build_fleet_table(sessions))
    console.print()
    console.print(build_external_table(externals))

def get_recent_logs(session_id: str, max_lines: int = 15) -> str:
    mgr = StateManager()
    s = mgr.get(session_id)
    if not s or not os.path.exists(s.log_file):
        return "(no log output yet)"
    try:
        with open(s.log_file, "r", errors="replace") as f:
            lines = f.readlines()
            return "".join(lines[-max_lines:])
    except Exception as e:
        return f"Error reading logs: {e}"

def run_live_dashboard(refresh_interval: float = 1.5) -> None:
    """Run interactive continuous live dashboard until Ctrl-C."""
    mgr = StateManager()
    reg = HarnessRegistry()

    with Live(console=console, screen=True, auto_refresh=False) as live:
        try:
            while True:
                sessions = mgr.list_all()
                externals = mgr.discover_external_harnesses(reg)
                table = build_fleet_table(sessions)
                ext_table = build_external_table(externals)

                # Layout: Top is table, middle is external, bottom is log preview
                layout = Layout()
                layout.split_column(
                    Layout(name="header", size=3),
                    Layout(name="tables", size=max(10, len(sessions) + len(externals) + 10)),
                    Layout(name="logs"),
                    Layout(name="footer", size=3)
                )

                layout["tables"].split_column(
                    Layout(name="managed"),
                    Layout(name="external")
                )

                layout["header"].update(Panel(
                    "[bold green]HMUX Dynamic Fleet Supervisor[/bold green] | Press [bold red]Ctrl+C[/bold red] to exit monitor",
                    style="blue"
                ))
                layout["tables"]["managed"].update(table)
                layout["tables"]["external"].update(ext_table)

                # Log preview for most recent running session
                active = next((s for s in sessions if s.status == "RUNNING"), None) or (sessions[0] if sessions else None)
                if active:
                    logs = get_recent_logs(active.session_id, max_lines=18)
                    layout["logs"].update(Panel(
                        Text.from_ansi(logs),
                        title=f"Live Log Stream: {active.session_id} ({active.harness})",
                        border_style="yellow"
                    ))
                else:
                    layout["logs"].update(Panel("(no sessions currently running)", title="Live Log Stream"))

                layout["footer"].update(Panel(
                    "[bold cyan]Quick Commands:[/bold cyan]  hmux spawn -H <harness>  |  hmux attach <id>  |  hmux diff <id>  |  hmux kill <id|pid>",
                    style="dim"
                ))

                live.update(layout)
                live.refresh()
                time.sleep(refresh_interval)
        except KeyboardInterrupt:
            pass
