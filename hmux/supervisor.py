"""
Fleet Supervisor & Session Attacher
Manages background worker processes, PTY streaming, interactive attachment, and lifecycles.
"""

import os
import sys
import tty
import time
import json
import socket
import select
import signal
import psutil
import termios
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from hmux.state import FleetSession, StateManager
from hmux.registry import HarnessRegistry
from hmux.worktree import WorktreeManager
from hmux.handover import HandoverEngine

class Supervisor:
    def __init__(self):
        self.state = StateManager()
        self.registry = HarnessRegistry()
        self.handover = HandoverEngine()

    def spawn(
        self,
        harness_name: str,
        task_name: Optional[str] = None,
        prompt: Optional[str] = None,
        worktree_branch: Optional[str] = None,
        from_harness: Optional[str] = None,
        from_session: Optional[str] = None,
        headless: bool = False,
        extra_args: Optional[List[str]] = None,
        use_worktree: bool = True,
        model: Optional[str] = None,
    ) -> FleetSession:
        """
        Spawn a new harness instance in an isolated worktree and background PTY daemon.
        """
        # Validate harness
        harness = self.registry.get(harness_name)
        if not harness:
            raise ValueError(f"Harness '{harness_name}' is not recognized or not installed.")

        # Determine task/branch name
        effective_task = task_name or f"{harness.key}-{int(time.time())}"
        session_id = self.state.generate_id(harness.key)

        # Handle zero-token handover if requested
        effective_prompt = prompt or ""
        if from_harness:
            if self.handover.is_available:
                try:
                    effective_prompt = self.handover.generate_handover_prompt(
                        from_harness, from_session, effective_prompt
                    )
                except Exception as e:
                    print(f"Warning: Handover context extraction failed: {e}")

        # Set up worktree or cwd
        branch_name = None
        worktree_path = os.getcwd()

        if use_worktree:
            wt_mgr = WorktreeManager()
            if wt_mgr.is_git_repo:
                try:
                    worktree_path, branch_name = wt_mgr.create_worktree(effective_task)
                except Exception as e:
                    print(f"Warning: Failed to create git worktree ({e}), using current directory.")
            elif harness.key == "freebuff" and worktree_path == str(Path.home()):
                default_fb_dir = os.path.join(str(Path.home()), "Projects", "freebuff")
                if os.path.exists(default_fb_dir):
                    worktree_path = default_fb_dir

        # Resolve actual model name for tracking
        effective_model = self.registry.resolve_model(harness.key, model)

        # Build execution command
        cmd, env = self.registry.build_command(
            harness_name=harness.key,
            worktree_dir=worktree_path,
            prompt=effective_prompt,
            headless=headless,
            extra_args=extra_args,
            model=model
        )

        session = FleetSession(
            session_id=session_id,
            harness=harness.key,
            task_name=effective_task,
            prompt=effective_prompt,
            worktree_path=worktree_path,
            branch=branch_name,
            status="RUNNING",
            model=effective_model
        )

        # Pure Zero-PTY headless invocation: for harnesses with native CLI non-interactive modes (e.g. codex exec)
        # Note: TUI harnesses like Freebuff require PTY execution of their official binary to comply with client/ad terms
        if headless and harness.key != "freebuff":
            with open(session.log_file, "a", encoding="utf-8") as log_out, \
                 open(session.log_file + ".err", "a", encoding="utf-8") as log_err:
                proc = subprocess.Popen(
                    cmd,
                    cwd=worktree_path,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_out,
                    stderr=log_err,
                    start_new_session=True,
                    close_fds=True
                )
            session.pid = proc.pid
            session.metadata["headless"] = True
            session.metadata["zero_pty"] = True
            self.state.register(session)
            return session

        # Launch background worker for interactive PTY sessions
        worker_script = str(Path(__file__).parent / "worker.py")

        worker_args = [
            sys.executable,
            worker_script,
            session_id,
            worktree_path,
            session.log_file,
            session.socket_path,
        ] + cmd

        # Detached process invocation
        with open(session.log_file + ".err", "w") as err_file:
            proc = subprocess.Popen(
                worker_args,
                stdin=subprocess.DEVNULL,
                stdout=err_file,
                stderr=err_file,
                start_new_session=True,
                close_fds=True
            )

        # Wait briefly for socket and pid
        pid_file = session.socket_path + ".pid"
        child_pid = proc.pid
        for _ in range(20):
            time.sleep(0.05)
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        child_pid = int(f.read().strip())
                        break
                except Exception:
                    pass

        session.pid = child_pid
        self.state.register(session)

        # Automated prompt injection for TUI harnesses that lack native CLI prompt flags
        if harness.key == "freebuff" and effective_prompt:
            from hmux.freebuff import FreebuffDriver
            driver = FreebuffDriver()
            if headless:
                import threading
                def _bg_headless_converse():
                    if driver.wait_until_ready(session, timeout=25.0):
                        resp = driver.converse(session, effective_prompt, timeout=120.0)
                        if resp:
                            with open(session.log_file, "a", encoding="utf-8") as f:
                                f.write(f"\n[hmux-freebuff-response]\n{resp}\n")
                        session.status = "COMPLETED"
                        self.state.update(session)
                threading.Thread(target=_bg_headless_converse, daemon=True).start()
            else:
                import threading
                def _bg_inject():
                    if driver.wait_until_ready(session, timeout=25.0):
                        driver.send_prompt(session, effective_prompt)
                threading.Thread(target=_bg_inject, daemon=True).start()

        return session

    def attach(self, session_id: str) -> None:
        """
        Interactively attach current terminal to the harness's PTY session.
        Press Ctrl-] to detach.
        """
        session = self.state.get(session_id)
        if not session:
            print(f"Error: Session '{session_id}' not found.")
            return

        if not os.path.exists(session.socket_path):
            if session.metadata.get("headless") or session.metadata.get("zero_pty"):
                print(f"[hmux] Session '{session_id}' is running in Zero-PTY headless mode (no terminal attached).")
                print(f"  • View logs:    hmux logs {session_id}")
                print(f"  • Tail output:  tail -f {session.log_file}")
                return
            if session.metadata.get("ambient"):
                print(f"[hmux] Session '{session_id}' is an ambient registered session (communicates via hmux talk/inbox).")
                print(f"  • Send input:  hmux talk {session_id} \"<message>\"")
                print(f"  • View inbox:  hmux inbox --session {session_id}")
                print(f"  • To spawn an attachable terminal session: hmux spawn --harness {session.harness} --task {session.task_name}")
            else:
                print(f"Error: Session socket not found. The process may have exited.")
                print(f"Check logs with: hmux logs {session_id}")
            return

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(session.socket_path)
        except Exception as e:
            print(f"Failed to connect to session socket: {e}")
            return

        print(f"\x1b[32m[hmux] Attached to {session.harness} ({session.session_id}). Press Ctrl-] to detach.\x1b[0m\r\n")

        # Set raw terminal mode if interactive
        is_tty = sys.stdin.isatty()
        old_settings = None
        if is_tty:
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin.fileno())

        try:
            while True:
                rlist, _, _ = select.select([sys.stdin.fileno(), sock.fileno()], [], [])

                if sock.fileno() in rlist:
                    data = sock.recv(4096)
                    if not data:
                        # Session closed
                        break
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()

                if sys.stdin.fileno() in rlist:
                    data = os.read(sys.stdin.fileno(), 1024)
                    if not data:
                        break
                    # Ctrl-] escape key (ASCII 29 / 0x1D)
                    if b"\x1d" in data:
                        break
                    sock.sendall(data)
        finally:
            if is_tty and old_settings:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            sock.close()
            print("\r\n\x1b[33m[hmux] Detached from session.\x1b[0m")

    def send_input(self, session_id: str, message: str) -> bool:
        """Send text input to a running session without attaching."""
        session = self.state.get(session_id)
        if not session or not os.path.exists(session.socket_path):
            return False

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(session.socket_path)
            if not message.endswith("\n"):
                message += "\n"
            sock.sendall(message.encode("utf-8"))
            sock.close()
            return True
        except Exception:
            return False

    def kill(self, session_id: str, remove_worktree: bool = False) -> bool:
        """Kill a session process (or raw PID) and optionally clean its worktree."""
        # Check if caller passed a raw external PID
        if session_id.isdigit():
            raw_pid = int(session_id)
            if psutil.pid_exists(raw_pid):
                try:
                    p = psutil.Process(raw_pid)
                    for child in p.children(recursive=True):
                        try:
                            child.terminate()
                        except Exception:
                            pass
                    p.terminate()
                    return True
                except Exception:
                    return False
            return False

        session = self.state.get(session_id)
        if not session:
            return False

        if session.pid and psutil.pid_exists(session.pid):
            # Guard against killing PID 1 or ambient harnesses
            if session.pid > 1 and session.pid != os.getpid() and not session.metadata.get("ambient"):
                try:
                    parent = psutil.Process(session.pid)
                    # Terminate children first
                    for child in parent.children(recursive=True):
                        try:
                            child.terminate()
                        except Exception:
                            pass
                    parent.terminate()
                except Exception:
                    pass

        # Keep COMPLETED status if it finished naturally
        if session.status != "COMPLETED":
            session.status = "STOPPED"
        self.state.update(session)

        # Cleanup socket
        if os.path.exists(session.socket_path):
            try:
                os.unlink(session.socket_path)
            except OSError:
                pass

        if remove_worktree and session.worktree_path and session.branch:
            wt_mgr = WorktreeManager()
            wt_mgr.remove_worktree(session.worktree_path, delete_branch=False, branch_name=session.branch)

        return True
