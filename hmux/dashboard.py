"""
HMUX Fleet Dashboard & Terminal UI
Rich-powered live visualization of all running and historical harnesses.
"""

import os
import time
import psutil
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.live import Live

from hmux.state import StateManager, FleetSession, ExternalHarness
from hmux.registry import HarnessRegistry

console = Console()

HARNESS_COLORS = {
    "antigravity": "bold cyan",
    "hermes": "bold magenta",
    "codex": "bold green",
    "opencode": "bold yellow",
    "pi": "bold blue",
    "freebuff": "bold red",
}

def format_harness_badge(harness: str) -> Text:
    clean = (harness or "").lower().strip()
    color = HARNESS_COLORS.get(clean, "bold white")
    label = clean.capitalize()
    if clean == "opencode":
        label = "OpenCode"
    elif clean == "freebuff":
        label = "Freebuff"
    return Text(f"● {label}", style=color)

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

def format_uptime(start_time_iso: Optional[str]) -> str:
    if not start_time_iso:
        return "-"
    try:
        dt = datetime.fromisoformat(start_time_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        seconds = max(0, int(delta.total_seconds()))
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        mins_rem = minutes % 60
        if hours < 24:
            return f"{hours}h {mins_rem}m" if mins_rem else f"{hours}h"
        days = hours // 24
        hours_rem = hours % 24
        return f"{days}d {hours_rem}h" if hours_rem else f"{days}d"
    except Exception:
        return "-"

def format_model_name(model: Optional[str]) -> str:
    if not model or model == "-":
        return "-"
    clean = model.strip()
    for prefix in ("opencode/", "reflection/", "anthropic/", "openai/", "google/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    if len(clean) > 28:
        clean = clean[:25] + "..."
    return clean

def format_mode(session: FleetSession) -> Text:
    if session.metadata.get("ambient"):
        return Text("ambient", style="dim cyan")
    if session.metadata.get("headless") or session.metadata.get("zero_pty"):
        return Text("headless", style="dim yellow")
    return Text("pty", style="dim green")

def extract_model_from_cmdline(cmdline: str) -> Optional[str]:
    parts = cmdline.split()
    for i, part in enumerate(parts):
        if part in ("-m", "--model") and i + 1 < len(parts):
            return parts[i + 1]
    return None

def filter_and_deduplicate_sessions(
    sessions: List[FleetSession],
    show_all: bool = False
) -> List[FleetSession]:
    """
    Remove redundant native session entries (e.g. raw local-... when a managed
    session tracks the same native ID) and filter by active/alive status.
    """
    # 1. Map managed native session IDs
    managed_native_ids = set()
    for s in sessions:
        nid = s.metadata.get("native_session_id")
        if nid and not s.session_id.startswith(("local-", "ses_")):
            managed_native_ids.add(nid)

    # 2. Deduplicate sessions
    deduped: List[FleetSession] = []
    seen_keys = set()
    for s in sessions:
        # Skip raw native ID session if a managed session exists for it
        if s.session_id.startswith(("local-", "ses_")) and s.session_id in managed_native_ids:
            continue

        nid = s.metadata.get("native_session_id")
        key = nid if nid else s.session_id
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(s)

    # 3. Filter by active status unless show_all is requested
    if not show_all:
        active = [s for s in deduped if s.status == "RUNNING" and s.is_alive()]
        return active

    return deduped

def build_fleet_table(
    sessions: List[FleetSession],
    reg: Optional[HarnessRegistry] = None,
    title: str = "🤖 HMUX Active Harness Fleet",
    width: Optional[int] = None
) -> Table:
    if reg is None:
        reg = HarnessRegistry()

    term_width = width or console.width or 80

    if term_width < 100:
        # Compact 4-column layout optimized for <= 80-column terminals
        table = Table(
            title=title,
            header_style="bold magenta",
            border_style="bright_black",
            expand=True
        )
        table.add_column("Session ID", style="cyan", no_wrap=True)
        table.add_column("Harness & Model")
        table.add_column("Task & Workspace")
        table.add_column("Status & Resources")

        if not sessions:
            table.add_row("-", "-", "No active sessions", "-")
            return table

        for s in sessions:
            branch_str = s.branch or (os.path.basename(s.worktree_path) if s.worktree_path else "main")
            resolved_model = format_model_name(s.model or reg.resolve_model(s.harness))
            uptime_str = format_uptime(s.start_time)
            harness_badge = format_harness_badge(s.harness)
            mode_badge = format_mode(s)

            # Harness & Model cell
            h_cell = Text()
            h_cell.append_text(harness_badge)
            h_cell.append(f"\n{resolved_model}", style="dim")

            # Task & Workspace cell
            t_cell = Text()
            t_cell.append(s.task_name or "Session", style="bold white")
            t_cell.append(f"\n{branch_str}", style="dim")

            # Status & Resources cell
            s_cell = Text()
            s_cell.append_text(format_status(s.status))
            res_str = format_resource(s.pid) if (s.status == "RUNNING" and s.is_alive()) else "-"
            pid_str = f"PID {s.pid}" if (s.pid and (s.status == "RUNNING" and s.is_alive())) else "-"
            if s.status == "RUNNING":
                s_cell.append(f"\n{pid_str} · {res_str} · {uptime_str}", style="dim")
            else:
                s_cell.append(f"\nended · {uptime_str} ago", style="dim")

            # Session ID (truncate raw native IDs to prevent wide blowout)
            sid = s.session_id
            if len(sid) > 22 and sid.startswith(("local-", "ses_")):
                sid = f"{sid[:14]}...{sid[-4:]}"

            table.add_row(sid, h_cell, t_cell, s_cell)

        return table

    else:
        # Full 7-column layout for >= 100-column terminals
        table = Table(
            title=title,
            header_style="bold magenta",
            border_style="bright_black",
            expand=True
        )
        table.add_column("Session ID", style="cyan", no_wrap=True)
        table.add_column("Harness", no_wrap=True)
        table.add_column("Model", style="bright_white", no_wrap=True)
        table.add_column("Task / Workspace")
        table.add_column("Status", justify="center")
        table.add_column("Resources", justify="right")
        table.add_column("Uptime", justify="center", style="blue")

        if not sessions:
            table.add_row("-", "-", "-", "No active sessions", "-", "-", "-")
            return table

        for s in sessions:
            branch_str = s.branch or (os.path.basename(s.worktree_path) if s.worktree_path else "main")
            resolved_model = format_model_name(s.model or reg.resolve_model(s.harness))
            uptime_str = format_uptime(s.start_time)
            harness_badge = format_harness_badge(s.harness)
            mode_badge = format_mode(s)

            # Harness + mode badge
            h_cell = Text()
            h_cell.append_text(harness_badge)
            h_cell.append(" ")
            h_cell.append_text(mode_badge)

            # Task & path
            t_cell = Text()
            t_cell.append(s.task_name or "Session", style="bold white")
            t_cell.append(f" ({branch_str})", style="dim")

            # Resources
            res_str = format_resource(s.pid) if (s.status == "RUNNING" and s.is_alive()) else "-"
            pid_str = f"PID {s.pid}" if (s.pid and (s.status == "RUNNING" and s.is_alive())) else "-"
            r_cell = Text()
            r_cell.append(f"{pid_str}\n", style="dim cyan")
            r_cell.append(res_str, style="dim")

            sid = s.session_id
            if len(sid) > 22 and sid.startswith(("local-", "ses_")):
                sid = f"{sid[:14]}...{sid[-4:]}"

            table.add_row(
                sid,
                h_cell,
                resolved_model,
                t_cell,
                format_status(s.status),
                r_cell,
                uptime_str
            )

        return table

def build_external_table(
    externals: List[ExternalHarness],
    reg: Optional[HarnessRegistry] = None,
    width: Optional[int] = None
) -> Table:
    if reg is None:
        reg = HarnessRegistry()

    term_width = width or console.width or 80

    if term_width < 100:
        table = Table(
            title="🌐 Ambient / External Harnesses (Detected in OS)",
            header_style="bold blue",
            border_style="bright_black",
            expand=True
        )
        table.add_column("Harness & Model", width=18)
        table.add_column("PID & Resources", width=16)
        table.add_column("Working Directory (CWD)", ratio=1)
        table.add_column("Command Preview", ratio=1)

        if not externals:
            table.add_row("-", "-", "No unmanaged harness processes running", "-")
            return table

        for e in externals:
            cmd_prev = e.cmdline[:50] + "..." if len(e.cmdline) > 50 else e.cmdline
            model = format_model_name(extract_model_from_cmdline(e.cmdline) or reg.resolve_model(e.harness))
            h_cell = Text()
            h_cell.append_text(format_harness_badge(e.harness))
            h_cell.append(f"\n{model}", style="dim")

            r_cell = Text(f"PID {e.pid}\n{e.cpu_percent:.1f}% | {e.memory_mb:.0f}MB", style="dim")

            table.add_row(h_cell, r_cell, e.cwd, cmd_prev)

        return table

    else:
        table = Table(
            title="🌐 Ambient / External Harnesses (Detected in OS)",
            header_style="bold blue",
            border_style="bright_black",
            expand=True
        )
        table.add_column("Harness", no_wrap=True)
        table.add_column("Model", style="bright_white", no_wrap=True)
        table.add_column("PID", justify="right", style="cyan")
        table.add_column("Working Directory (CWD)", style="green")
        table.add_column("Resource Usage", justify="right")
        table.add_column("Command Preview", style="dim")

        if not externals:
            table.add_row("-", "-", "-", "No unmanaged harness processes running", "-", "-")
            return table

        for e in externals:
            cmd_prev = e.cmdline[:60] + "..." if len(e.cmdline) > 60 else e.cmdline
            model = format_model_name(extract_model_from_cmdline(e.cmdline) or reg.resolve_model(e.harness))
            harness_badge = format_harness_badge(e.harness)
            table.add_row(
                harness_badge,
                model,
                str(e.pid),
                e.cwd,
                f"{e.cpu_percent:.1f}% | {e.memory_mb:.0f}MB",
                cmd_prev
            )
        return table

def get_fleet_sessions(
    mgr: StateManager,
    reg: HarnessRegistry,
    show_all: bool = False
) -> Tuple[List[FleetSession], List[Any], List[FleetSession]]:
    """
    Retrieve and merge managed and ambient sessions, deduplicating
    and refreshing live process state across all supported harnesses.
    Returns: (active_sessions, remaining_externals, combined_sessions)
    """
    all_sessions = mgr.list_all(refresh=True)
    ambient_sessions = mgr.discover_ambient_sessions(reg)

    session_map: Dict[str, FleetSession] = {}
    for s in all_sessions:
        s.update_status()
        session_map[s.session_id] = s

    for amb in ambient_sessions:
        matched_key = None
        for sid, s in session_map.items():
            if sid == amb.session_id:
                matched_key = sid
                break
            amb_nid = amb.metadata.get("native_session_id")
            s_nid = s.metadata.get("native_session_id")
            if amb_nid and s_nid and amb_nid == s_nid:
                matched_key = sid
                break

        if matched_key:
            existing = session_map[matched_key]
            if amb.is_alive():
                # Ambient session is actively running; promote/update existing session state
                existing.status = "RUNNING"
                existing.end_time = None
                if amb.pid:
                    existing.pid = amb.pid
                if amb.model:
                    existing.model = amb.model
                if amb.task_name and (not existing.task_name or existing.task_name in ("Agent Task", "OpenCode Interactive")):
                    existing.task_name = amb.task_name
                if amb.worktree_path:
                    existing.worktree_path = amb.worktree_path
                existing.metadata.update(amb.metadata)
                try:
                    mgr.save(existing)
                except Exception:
                    pass
        else:
            session_map[amb.session_id] = amb

    combined_sessions = list(session_map.values())
    active_sessions = filter_and_deduplicate_sessions(combined_sessions, show_all=show_all)

    externals = mgr.discover_external_harnesses(reg)
    surfaced_pids = {s.pid for s in active_sessions if s.pid}
    surfaced_harnesses = {s.harness for s in active_sessions}
    remaining_externals = [
        e for e in externals
        if e.pid not in surfaced_pids and e.harness not in surfaced_harnesses
    ]

    return active_sessions, remaining_externals, combined_sessions

def print_status_table(show_all: bool = False) -> None:
    """Print clean, non-redundant status table to terminal."""
    mgr = StateManager()
    reg = HarnessRegistry()
    installed = [h.key for h in reg.list_installed()]

    active_sessions, remaining_externals, combined_sessions = get_fleet_sessions(mgr, reg, show_all=show_all)

    # Header summary line
    active_count = len([s for s in combined_sessions if s.status == "RUNNING" and s.is_alive()])
    total_historical = len([s for s in combined_sessions if s.status in ("COMPLETED", "STOPPED", "FAILED") and not s.is_alive()])
    status_tag = f"[bold green]{active_count} running[/bold green]" if active_count else "[dim yellow]0 running[/dim yellow]"
    console.print(
        f"[bold cyan]HMUX[/bold cyan] - Agent Harness Fleet Manager | "
        f"Active: {status_tag} | "
        f"Installed: [bold yellow]{', '.join(installed)}[/bold yellow]"
    )

    if active_sessions:
        title = "🤖 HMUX All Fleet Sessions" if show_all else f"🤖 HMUX Active Harness Fleet ({len(active_sessions)} active)"
        console.print(build_fleet_table(active_sessions, reg=reg, title=title))
    else:
        # Show an elegant, clean empty state panel
        panel = Panel(
            Text.from_markup(
                f"[bold green]✔ Fleet Idle[/bold green] — No active agent sessions currently running.\n\n"
                f"[bold white]• Installed Harnesses :[/bold white] [yellow]{', '.join(installed)}[/yellow]\n"
                f"[bold white]• Historical Sessions :[/bold white] [cyan]{total_historical} completed[/cyan] [dim](run 'hmux sessions' or 'hmux status -a' to inspect)[/dim]\n"
                f"[bold white]• Launch an Agent     :[/bold white] [bold cyan]hmux spawn -H <harness> -t <task> -p \"<prompt>\"[/bold cyan]"
            ),
            title="🤖 HMUX Harness Fleet Status",
            title_align="center",
            border_style="blue",
            padding=(1, 2)
        )
        console.print(panel)

    console.print()

    # Ambient / External Harnesses (any remaining external processes not represented above)
    if remaining_externals:
        console.print(build_external_table(remaining_externals, reg=reg))

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
                active_sessions, remaining_externals, _ = get_fleet_sessions(mgr, reg, show_all=False)
                table = build_fleet_table(active_sessions, reg=reg)

                # Layout: Top is table, middle is log preview, bottom is footer
                layout = Layout()
                table_size = max(8, len(active_sessions) + (len(remaining_externals) + 6 if remaining_externals else 0) + 4)
                layout.split_column(
                    Layout(name="header", size=3),
                    Layout(name="tables", size=table_size),
                    Layout(name="logs"),
                    Layout(name="footer", size=3)
                )

                layout["header"].update(Panel(
                    "[bold green]HMUX Dynamic Fleet Supervisor[/bold green] | Press [bold red]Ctrl+C[/bold red] to exit monitor",
                    style="blue"
                ))

                if remaining_externals:
                    layout["tables"].split_column(
                        Layout(name="managed"),
                        Layout(name="external")
                    )
                    ext_table = build_external_table(remaining_externals, reg=reg)
                    layout["tables"]["managed"].update(table)
                    layout["tables"]["external"].update(ext_table)
                else:
                    layout["tables"].update(table)

                # Log preview for most recent running session
                active = next((s for s in active_sessions if s.status == "RUNNING"), None) or (active_sessions[0] if active_sessions else None)
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
