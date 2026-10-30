"""
HMUX Command Line Interface
Main user-facing entrypoint for fleet orchestration, spawning, attachment, and worktree control.
"""

import os
import sys
import time
import click
from rich.console import Console
from rich.table import Table

from hmux.supervisor import Supervisor
from hmux.registry import HarnessRegistry
from hmux.worktree import WorktreeManager
from hmux.state import StateManager
from hmux.handover import HandoverEngine
from hmux.dashboard import print_status_table, run_live_dashboard
from hmux.router import MessageRouter
from hmux.catalog import SessionCatalog

console = Console()

@click.group()
def cli():
    """🤖 HMUX - Universal AI Coding Agent Harness Fleet Manager"""
    pass

@cli.command("status")
def status_cmd():
    """Display active agent sessions, resource usage, and statuses."""
    print_status_table()

@cli.command("top")
@click.option("--interval", "-i", default=1.5, type=float, help="Refresh interval in seconds")
def top_cmd(interval: float):
    """Launch interactive real-time fleet dashboard."""
    run_live_dashboard(interval)

@cli.command("harnesses")
def harnesses_cmd():
    """List all detected AI coding harnesses installed on this system."""
    reg = HarnessRegistry()
    installed = reg.list_installed()

    table = Table(title="Detected AI Coding Harnesses", header_style="bold magenta")
    table.add_column("Harness Key", style="cyan")
    table.add_column("Binary Path", style="green")
    table.add_column("Description", style="white")

    for h in installed:
        table.add_row(h.key, h.binary_path, h.description)

    console.print(table)

@cli.command("spawn")
@click.option("--harness", "-H", required=True, help="Target harness (e.g. codex, opencode, agy, hermes, pi, freebuff)")
@click.option("--task", "-t", default=None, help="Task name for branch & worktree (e.g. auth-fix)")
@click.option("--prompt", "-p", default=None, help="Initial prompt or instruction for the harness")
@click.option("--model", "-m", default=None, help="Specific AI model to use (e.g. gpt-4o, claude-3-5-sonnet, flint-free)")
@click.option("--from-harness", default=None, help="Extract handover context from this harness (via session-transmuter)")
@click.option("--from-session", default=None, help="Specific session ID for handover")
@click.option("--no-worktree", is_flag=True, default=False, help="Run directly in current directory instead of worktree")
@click.option("--headless", is_flag=True, default=False, help="Run harness non-interactively if supported")
@click.option("--attach", "-a", is_flag=True, default=False, help="Attach interactively immediately after spawning")
def spawn_cmd(
    harness: str,
    task: str,
    prompt: str,
    model: str,
    from_harness: str,
    from_session: str,
    no_worktree: bool,
    headless: bool,
    attach: bool
):
    """Spawn a harness inside an isolated Git worktree and background PTY supervisor."""
    sup = Supervisor()
    try:
        session = sup.spawn(
            harness_name=harness,
            task_name=task,
            prompt=prompt,
            from_harness=from_harness,
            from_session=from_session,
            headless=headless,
            use_worktree=not no_worktree,
            model=model
        )

        console.print(f"[bold green]✔ Successfully spawned {session.harness} session:[/bold green] [bold cyan]{session.session_id}[/bold cyan]")
        console.print(f"  • Task / Branch: [bold yellow]{session.task_name}[/bold yellow] ({session.branch or 'cwd'})")
        if session.model:
            console.print(f"  • Model: [bold green]{session.model}[/bold green]")
        console.print(f"  • Worktree: [dim]{session.worktree_path}[/dim]")
        console.print(f"  • Log file: [dim]{session.log_file}[/dim]")
        console.print(f"  • Attach: [cyan]hmux attach {session.session_id}[/cyan]")

        if attach:
            sup.attach(session.session_id)

    except Exception as e:
        console.print(f"[bold red]Error spawning harness:[/bold red] {e}")
        sys.exit(1)

@cli.command("attach")
@click.argument("session_id")
def attach_cmd(session_id: str):
    """Attach terminal directly to a running harness session (Ctrl-] to detach)."""
    sup = Supervisor()
    sup.attach(session_id)

@cli.command("send")
@click.argument("session_id")
@click.argument("message")
def send_cmd(session_id: str, message: str):
    """Send text input / response into a running harness session."""
    sup = Supervisor()
    if sup.send_input(session_id, message):
        console.print(f"[bold green]✔ Sent message to {session_id}[/bold green]")
    else:
        console.print(f"[bold red]✖ Failed to send message. Is session {session_id} running?[/bold red]")

@cli.command("logs")
@click.argument("session_id")
@click.option("--follow", "-f", is_flag=True, help="Follow log output continuously")
@click.option("--lines", "-n", default=30, type=int, help="Number of trailing lines to view")
def logs_cmd(session_id: str, follow: bool, lines: int):
    """View or tail the console logs of an agent session."""
    mgr = StateManager()
    session = mgr.get(session_id)
    if not session:
        console.print(f"[bold red]Session '{session_id}' not found.[/bold red]")
        sys.exit(1)

    log_path = session.log_file
    if not os.path.exists(log_path):
        console.print("[dim](No log output recorded yet)[/dim]")
        return

    if not follow:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
            console.print("".join(all_lines[-lines:]), end="")
    else:
        console.print(f"[dim]Following logs for {session_id}... Press Ctrl+C to stop.[/dim]\n")
        try:
            with open(log_path, "r", errors="replace") as f:
                # Seek to near end
                all_lines = f.readlines()
                for line in all_lines[-lines:]:
                    sys.stdout.write(line)
                sys.stdout.flush()
                while True:
                    line = f.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    else:
                        time.sleep(0.2)
        except KeyboardInterrupt:
            pass

@cli.command("diff")
@click.argument("session_id")
def diff_cmd(session_id: str):
    """Inspect Git diff of code modifications produced by the agent in its worktree."""
    mgr = StateManager()
    session = mgr.get(session_id)
    if not session:
        console.print(f"[bold red]Session '{session_id}' not found.[/bold red]")
        sys.exit(1)

    if not session.branch:
        console.print(f"[yellow]Session '{session_id}' did not use an isolated git branch.[/yellow]")
        return

    wt_mgr = WorktreeManager()
    try:
        diff_text = wt_mgr.get_diff(session.branch)
        if not diff_text.strip():
            console.print(f"[dim]No changes made yet on branch {session.branch}.[/dim]")
        else:
            console.print(f"[bold green]=== Git Diff for {session.session_id} ({session.branch}) ===[/bold green]\n")
            console.print(diff_text)
    except Exception as e:
        console.print(f"[bold red]Error checking diff:[/bold red] {e}")

@cli.command("merge")
@click.argument("session_id")
@click.option("--target-branch", "-b", default=None, help="Target branch to merge into (defaults to current)")
def merge_cmd(session_id: str, target_branch: str):
    """Merge an agent's completed branch back into your main/target branch."""
    mgr = StateManager()
    session = mgr.get(session_id)
    if not session or not session.branch:
        console.print(f"[bold red]Invalid session or session has no isolated branch.[/bold red]")
        sys.exit(1)

    wt_mgr = WorktreeManager()
    success, msg = wt_mgr.merge_worktree(session.branch, target_branch)
    if success:
        console.print(f"[bold green]✔ Successfully merged branch {session.branch}![/bold green]")
        console.print(msg)
    else:
        console.print(f"[bold red]✖ Merge failed or had conflicts:[/bold red]")
        console.print(msg)

@cli.command("handover")
@click.option("--from-session", "-s", required=True, help="Source harness name or session ID")
@click.option("--to-harness", "-t", required=True, help="Target harness to launch")
@click.option("--task", default=None, help="Task name for target worktree")
@click.option("--prompt", "-p", default="", help="Additional directive for target harness")
@click.option("--model", "-m", default=None, help="Specific AI model for target harness")
@click.option("--attach/--no-attach", "-a", default=False, help="Attach to new harness immediately")
def handover_cmd(from_session: str, to_harness: str, task: str, prompt: str, model: str, attach: bool):
    """Transfer state out-of-band via session-transmuter and spawn target harness."""
    sup = Supervisor()
    source_harness = from_session
    source_session_id = None

    # Check if user passed a managed session ID
    existing = sup.state.get(from_session)
    if existing:
        source_harness = existing.harness
        source_session_id = existing.session_id
    else:
        # Check if user passed a harness name or alias (e.g. antigravity, agy, codex)
        h_info = sup.registry.get(from_session)
        if h_info:
            source_harness = h_info.key
            source_session_id = None
        else:
            # It's an unmanaged session ID, pass it as session_id
            source_session_id = from_session

    console.print(f"[bold cyan]Transmuting state from {source_harness} to {to_harness}...[/bold cyan]")
    try:
        session = sup.spawn(
            harness_name=to_harness,
            task_name=task or f"handover-{to_harness}",
            prompt=prompt,
            from_harness=source_harness,
            from_session=source_session_id,
            use_worktree=True,
            model=model
        )
        console.print(f"[bold green]✔ Handover complete! Spawned {session.session_id}[/bold green]")
        if attach:
            sup.attach(session.session_id)
    except Exception as e:
        console.print(f"[bold red]Handover failed:[/bold red] {e}")

@cli.command("kill")
@click.argument("session_id")
@click.option("--clean-worktree", is_flag=True, default=False, help="Also remove the isolated worktree directory")
def kill_cmd(session_id: str, clean_worktree: bool):
    """Terminate a running harness session."""
    sup = Supervisor()
    sess = sup.state.get(session_id)
    was_completed = sess and sess.status == "COMPLETED"
    if sup.kill(session_id, remove_worktree=clean_worktree):
        if was_completed:
            console.print(f"[bold green]✔ Cleaned up completed session {session_id}[/bold green]")
        else:
            console.print(f"[bold green]✔ Stopped session {session_id}[/bold green]")
    else:
        console.print(f"[bold red]Session {session_id} not found.[/bold red]")

@cli.command("clean")
def clean_cmd():
    """Prune stopped or exited sessions from the state registry."""
    mgr = StateManager()
    sessions = mgr.list_all()
    cleaned = 0
    for s in sessions:
        if s.status in ("COMPLETED", "STOPPED", "FAILED"):
            mgr.delete(s.session_id)
            cleaned += 1
    console.print(f"[bold green]✔ Pruned {cleaned} inactive sessions.[/bold green]")

@cli.command("talk")
@click.argument("session_id")
@click.argument("message")
@click.option("--from-session", "--from", "-f", "from_session", default="ambient", help="Your sender session ID or harness name")
@click.option("--wait", "-w", is_flag=True, default=False, help="Wait for the target harness to reply and display response")
@click.option("--timeout", "-t", default=60.0, type=float, help="Seconds to wait for a reply (default: 60)")
def talk_cmd(session_id: str, message: str, from_session: str, wait: bool, timeout: float):
    """Deliver an in-session message to an active harness, with optional loopback reply waiting."""
    router = MessageRouter()
    if wait:
        console.print(f"[dim]Dispatching message to {session_id} and waiting for reply (timeout: {timeout}s)...[/dim]")
    success, res = router.send(
        to_session=session_id,
        message=message,
        from_session=from_session,
        wait=wait,
        timeout=timeout
    )
    if success:
        if wait:
            console.print(f"\n[bold green]=== Reply from {session_id} ===[/bold green]")
            console.print(res)
        else:
            console.print(f"[bold green]✔ {res}[/bold green]")
    else:
        console.print(f"[bold red]✖ {res}[/bold red]")
        sys.exit(1)

@cli.command("broadcast")
@click.argument("message")
@click.option("--from-session", "--from", "-f", "from_session", default="ambient", help="Sender session ID or harness name")
def broadcast_cmd(message: str, from_session: str):
    """Broadcast an announcement or alert to all active sessions in the fleet."""
    router = MessageRouter()
    res = router.broadcast(message=message, from_session=from_session)
    count = res["delivered_count"]
    recipients = ", ".join(res["recipients"]) if res["recipients"] else "None"
    console.print(f"[bold green]✔ Broadcast delivered to {count} session(s):[/bold green] [cyan]{recipients}[/cyan]")

@cli.command("register")
@click.option("--id", "-i", required=True, help="Session identifier for this chat session")
@click.option("--harness", "-H", default="antigravity", help="Harness name (antigravity, codex, pi, etc.)")
@click.option("--task", "-t", default="Interactive Session", help="Short description of task or focus")
def register_cmd(id: str, harness: str, task: str):
    """Register an ambient/manual session into HMUX so peer harnesses can discover and message it."""
    router = MessageRouter()
    session = router.register_ambient(session_id=id, harness=harness, task_name=task)
    console.print(f"[bold green]✔ Registered ambient session:[/bold green] [bold cyan]{session.session_id}[/bold cyan] ({session.harness}) - '{session.task_name}'")

@cli.command("sessions")
@click.option("--live-only", "-l", is_flag=True, default=False, help="Show only active running sessions")
@click.option("--harness", "-H", default=None, help="Filter by harness name (antigravity, codex, pi, etc.)")
@click.option("--limit", "-n", default=25, type=int, help="Max sessions to display")
def sessions_cmd(live_only: bool, harness: str, limit: int):
    """List fleet sessions with real titles, goals, and active chatting harnesses."""
    router = MessageRouter()
    catalog = SessionCatalog()

    # 1. Active chatting sessions
    active = router.list_active_sessions()
    reg = HarnessRegistry()
    if active or live_only:
        table_live = Table(title="🟢 Currently Active Chatting Harnesses", header_style="bold green", expand=True)
        table_live.add_column("Session ID", style="cyan")
        table_live.add_column("Harness", style="yellow")
        table_live.add_column("Model", style="bold blue")
        table_live.add_column("Task / Context", style="white")
        table_live.add_column("Branch", style="green")
        table_live.add_column("Status", style="bold green")

        if not active:
            table_live.add_row("-", "No active sessions running", "-", "-", "-", "-")
        else:
            for s in active:
                model_disp = s.get("model") or reg.resolve_model(s["harness"])
                table_live.add_row(
                    s["session_id"],
                    s["harness"],
                    model_disp,
                    s["task_name"],
                    s["branch"],
                    s["status"]
                )
        console.print(table_live)

    if live_only:
        return

    # 2. Universal catalog across all harnesses with titles
    all_sess = catalog.list_all_sessions(harness_filter=harness, limit=limit)
    table_cat = Table(title="📚 Discovered Sessions Across All Harnesses (with Titles)", header_style="bold magenta", expand=True)
    table_cat.add_column("Harness", style="bold yellow")
    table_cat.add_column("Session ID", style="cyan", no_wrap=True)
    table_cat.add_column("Conversation Title / Objective", style="white")
    table_cat.add_column("Updated", style="blue")
    table_cat.add_column("Live", justify="center")

    if not all_sess:
        table_cat.add_row("-", "No historical sessions found", "-", "-", "-")
    else:
        for item in all_sess:
            live_mark = "[bold green]● LIVE[/bold green]" if item["is_live"] else "[dim]idle[/dim]"
            title_disp = item["title"][:58] + "..." if len(item["title"]) > 58 else item["title"]
            table_cat.add_row(
                item["harness"],
                item["session_id"][:32],
                title_disp,
                item["updated_at"],
                live_mark
            )
    console.print(table_cat)
    console.print("[dim]Tip: Run 'hmux inspect <harness> <session_id>' to view full goal, touched files, and last turn.[/dim]")

@cli.command("inspect")
@click.argument("target")
@click.argument("secondary", required=False, default=None)
def inspect_cmd(target: str, secondary: Optional[str]):
    """View rich details of any session: goal, touched files, turn count, and last turn.
    Usage: hmux inspect <session_id>  OR  hmux inspect <harness> <session_id>
    """
    catalog = SessionCatalog()
    mgr = StateManager()

    if secondary is not None:
        harness = target
        session_id = secondary
    else:
        # User passed single argument: hmux inspect <session_id>
        session_id = target
        # Check if it exists in fleet state first
        state_sess = mgr.get(session_id)
        if state_sess:
            harness = state_sess.harness
        else:
            # Look up in discovered catalog
            all_s = catalog.list_all_sessions(limit=100)
            matched = [s for s in all_s if s["session_id"] == session_id or session_id in s["session_id"]]
            if matched:
                harness = matched[0]["harness"]
                session_id = matched[0]["session_id"]
            else:
                harness = "unknown"

    detail = catalog.inspect_session(harness=harness, session_id=session_id)
    state_sess = mgr.get(session_id)
    if not detail:
        # Check if it's an active registered ambient session in state manager
        if state_sess:
            detail = {
                "harness": state_sess.harness,
                "session_id": state_sess.session_id,
                "title": state_sess.task_name,
                "model": state_sess.model or "Default (configured in harness)",
                "workspace_root": state_sess.worktree_path or os.getcwd(),
                "total_turns": 0,
                "created_at": state_sess.start_time,
                "files_modified": [],
                "files_read": [],
                "last_turn": f"Fleet Status: {state_sess.status} ({'ambient' if state_sess.metadata.get('ambient') else 'PTY'})"
            }
        else:
            console.print(f"[bold red]Session '{session_id}' not found across any harness.[/bold red]")
            sys.exit(1)
    elif state_sess and state_sess.model:
        detail["model"] = state_sess.model

    table = Table(title=f"🔍 Session Inspection: {detail['harness'].upper()} → {detail['session_id']}", header_style="bold cyan")
    table.add_column("Field", style="bold yellow", width=18)
    table.add_column("Value", style="white")

    table.add_row("Harness", detail["harness"])
    table.add_row("Session ID", detail["session_id"])
    if detail.get("model"):
        table.add_row("Model", detail["model"])
    table.add_row("Title / Goal", detail.get("title", "-"))
    table.add_row("Workspace", detail.get("workspace_root", "-"))
    table.add_row("Turns Recorded", str(detail.get("total_turns", 0)))
    if detail.get("files_modified"):
        table.add_row("Files Modified", "\n".join(detail["files_modified"][:8]))
    if detail.get("files_read"):
        table.add_row("Files Read", "\n".join(detail["files_read"][:8]))
    if detail.get("last_turn"):
        table.add_row("Last Note / Turn", detail["last_turn"])

    console.print(table)

@cli.command("inbox")
@click.option("--session", "-s", default=None, help="Filter messages for a specific session ID")
@click.option("--limit", "-n", default=15, type=int, help="Number of messages to view")
def inbox_cmd(session: str, limit: int):
    """View recent inter-harness messages and replies."""
    router = MessageRouter()
    msgs = router.get_inbox(session_id=session, limit=limit)
    if not msgs:
        console.print("[dim]No inter-harness messages recorded.[/dim]")
        return
    table = Table(title="📬 Fleet Inter-Harness Message Log", header_style="bold blue")
    table.add_column("ID", style="dim")
    table.add_column("From", style="yellow")
    table.add_column("To", style="cyan")
    table.add_column("Message Preview", style="white")
    table.add_column("Status", style="magenta")
    table.add_column("Reply", style="green")
    for m in msgs:
        body_prev = m.get("body", "")[:40] + "..." if len(m.get("body", "")) > 40 else m.get("body", "")
        reply_prev = (m.get("reply", "")[:30] + "...") if m.get("reply") else "-"
        table.add_row(
            m.get("id", "-"),
            m.get("from_session", "-"),
            m.get("to_session", "-"),
            body_prev,
            m.get("status", "-"),
            reply_prev
        )
    console.print(table)

@cli.command("mcp")
def mcp_cmd():
    """Run the HMUX Model Context Protocol (MCP) server over stdio."""
    from hmux.mcp_server import run_mcp_server
    run_mcp_server()

if __name__ == "__main__":
    cli()
