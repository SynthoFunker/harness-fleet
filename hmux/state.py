"""
Persistent Fleet State Manager
Tracks active and historical harness sessions, process health, and workspaces.
"""

import os
import json
import uuid
import time
import socket
import sqlite3
import psutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

STATE_DIR = Path.home() / ".hmux"
STATE_FILE = STATE_DIR / "state.json"
LOGS_DIR = STATE_DIR / "logs"
SOCKETS_DIR = STATE_DIR / "sockets"

class FleetSession:
    def __init__(
        self,
        session_id: str,
        harness: str,
        task_name: str,
        prompt: str,
        worktree_path: str,
        branch: Optional[str] = None,
        pid: Optional[int] = None,
        status: str = "RUNNING",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        exit_code: Optional[int] = None,
        log_file: Optional[str] = None,
        socket_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ):
        self.session_id = session_id
        self.harness = harness
        self.task_name = task_name
        self.prompt = prompt
        self.worktree_path = worktree_path
        self.branch = branch
        self.pid = pid
        self.status = status
        self.start_time = start_time or datetime.now(timezone.utc).isoformat()
        self.end_time = end_time
        self.exit_code = exit_code
        self.log_file = log_file or str(LOGS_DIR / f"{session_id}.log")
        self.socket_path = socket_path or str(SOCKETS_DIR / f"{session_id}.sock")
        self.metadata = metadata or {}
        self.model = model

    def _opencode_session_is_busy(self, db_path: Optional[Path] = None) -> bool:
        """
        Return True only while this OpenCode session is actively running a turn.

        OpenCode TUIs share one server process, so PID checks cannot identify a
        specific session. The session_v2 busy/idle timestamps can.
        """
        session_id = self.metadata.get("native_session_id") or self.session_id
        db_path = db_path or (Path.home() / ".local" / "share" / "opencode" / "opencode.db")
        if not Path(db_path).exists():
            return False
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute(
                "SELECT time_busy, time_idle FROM session_v2 WHERE id = ? LIMIT 1;",
                (session_id,),
            )
            row = cur.fetchone()
            con.close()
            if not row:
                return False
            busy, idle = row
            return bool(busy) and (not idle or busy > idle)
        except Exception:
            return False

    def is_alive(self) -> bool:
        # 1. Check if Unix domain socket is active and accepting connections
        if self.socket_path and os.path.exists(self.socket_path):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.3)
                s.connect(self.socket_path)
                s.close()
                return True
            except Exception:
                pass

        # OpenCode TUIs share one server process, so a PID match cannot prove
        # this specific session is active. Use its busy/idle timestamps instead.
        if self.metadata.get("ambient") and self.harness == "opencode":
            return self._opencode_session_is_busy()

        from hmux.registry import HarnessRegistry
        reg = HarnessRegistry()

        # 2. Check PID directly if available
        if self.pid:
            try:
                p = psutil.Process(self.pid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    name = p.name().lower()
                    if self.metadata.get("ambient"):
                        matched = reg.match_process(name, p.cmdline())
                        if matched == self.harness:
                            return True
                    else:
                        cmd = " ".join(p.cmdline()).lower()
                        if self.session_id.lower() in cmd:
                            return True
                        matched = reg.match_process(name, p.cmdline())
                        if matched == self.harness:
                            return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # 3. Fallback for ambient sessions (PID difference across container/sandbox namespaces or PID recycled)
        if self.metadata.get("ambient"):
            # Check current process list for harness match
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    p_name = proc.info['name'] or ''
                    p_cmd = proc.info['cmdline'] or []
                    if reg.match_process(p_name, p_cmd) == self.harness:
                        self.pid = proc.info['pid']
                        return True
                except Exception:
                    continue

            # Ambient harness-specific active indicators (e.g. within isolated container PID namespaces)
            if self.harness == "hermes":
                hermes_active = Path.home() / ".hermes" / "runtime" / "active_sessions.json"
                if hermes_active.exists():
                    try:
                        data = json.loads(hermes_active.read_text())
                        if data.get("entries"):
                            return True
                    except Exception:
                        pass
            elif self.harness == "antigravity":
                if os.environ.get("ANTIGRAVITY_SESSION_ID") or (Path.home() / ".gemini" / "antigravity-cli").exists():
                    return True

        return False

    def update_status(self) -> None:
        """Refresh status based on OS process state."""
        if self.status == "RUNNING":
            if not self.is_alive():
                # Process exited
                self.status = "COMPLETED"
                if not self.end_time:
                    self.end_time = datetime.now(timezone.utc).isoformat()
        elif self.status in ("COMPLETED", "STOPPED") and self.is_alive():
            self.status = "RUNNING"
            self.end_time = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "harness": self.harness,
            "task_name": self.task_name,
            "prompt": self.prompt,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "pid": self.pid,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "exit_code": self.exit_code,
            "log_file": self.log_file,
            "socket_path": self.socket_path,
            "metadata": self.metadata,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FleetSession":
        return cls(**data)


class ExternalHarness:
    def __init__(
        self,
        harness: str,
        pid: int,
        cwd: str,
        cmdline: str,
        cpu_percent: float,
        memory_mb: float,
        create_time: str
    ):
        self.harness = harness
        self.pid = pid
        self.cwd = cwd
        self.cmdline = cmdline
        self.cpu_percent = cpu_percent
        self.memory_mb = memory_mb
        self.create_time = create_time


class StateManager:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        SOCKETS_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not STATE_FILE.exists():
            self._write_raw({"sessions": {}})

    def _read_raw(self) -> Dict[str, Any]:
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"sessions": {}}

    def _write_raw(self, data: Dict[str, Any]) -> None:
        temp = STATE_FILE.with_suffix(".tmp")
        with open(temp, "w") as f:
            json.dump(data, f, indent=2)
        temp.replace(STATE_FILE)

    def generate_id(self, harness: str) -> str:
        short = uuid.uuid4().hex[:6]
        return f"{harness}-{short}"

    def register(self, session: FleetSession) -> None:
        data = self._read_raw()
        data["sessions"][session.session_id] = session.to_dict()
        self._write_raw(data)

    def get(self, session_id: str) -> Optional[FleetSession]:
        data = self._read_raw()
        item = data["sessions"].get(session_id)
        if item:
            s = FleetSession.from_dict(item)
            s.update_status()
            if s.status != item["status"]:
                self.update(s)
            return s
        return None

    def update(self, session: FleetSession) -> None:
        data = self._read_raw()
        data["sessions"][session.session_id] = session.to_dict()
        self._write_raw(data)

    def deduplicate(self) -> int:
        """Prune redundant session entries from state (e.g. duplicate native session IDs)."""
        data = self._read_raw()
        sessions = data.get("sessions", {})
        managed_native_ids = set()
        for sid, sdata in sessions.items():
            nid = sdata.get("metadata", {}).get("native_session_id")
            if nid and not sid.startswith(("local-", "ses_")):
                managed_native_ids.add(nid)

        to_delete = []
        for sid in sessions:
            if sid.startswith(("local-", "ses_")) and sid in managed_native_ids:
                to_delete.append(sid)

        for sid in to_delete:
            del sessions[sid]

        if to_delete:
            self._write_raw(data)
        return len(to_delete)

    def list_all(self, refresh: bool = True) -> List[FleetSession]:
        if refresh:
            self.deduplicate()
        data = self._read_raw()
        sessions = [FleetSession.from_dict(d) for d in data["sessions"].values()]
        changed = False
        if refresh:
            for s in sessions:
                old = s.status
                s.update_status()
                if s.status != old:
                    data["sessions"][s.session_id] = s.to_dict()
                    changed = True
        if changed:
            self._write_raw(data)
        # Sort by start_time descending
        sessions.sort(key=lambda s: s.start_time or "", reverse=True)
        return sessions

    def delete(self, session_id: str) -> bool:
        data = self._read_raw()
        if session_id in data["sessions"]:
            del data["sessions"][session_id]
            self._write_raw(data)
            return True
        return False

    def discover_external_harnesses(self, registry) -> List[ExternalHarness]:
        """
        Scan system processes to detect any unmanaged harness processes running
        outside of hmux (e.g. launched manually in other terminals).
        """
        managed_pids = set()
        for s in self.list_all(refresh=True):
            if s.pid and s.status == "RUNNING":
                managed_pids.add(s.pid)

        current_pid = os.getpid()
        external = []

        for p in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd', 'create_time', 'cpu_percent', 'memory_info']):
            try:
                pid = p.info['pid']
                if pid == current_pid or pid in managed_pids:
                    continue

                cmdline_list = p.info['cmdline'] or []
                proc_name = p.info['name'] or ""

                if not cmdline_list and not proc_name:
                    continue

                # Fast check to ignore known audio/multimedia and system daemons
                if proc_name.lower().startswith("pipewire") or proc_name.lower() in ("wireplumber", "pulseaudio"):
                    continue

                harness_key = registry.match_process(proc_name, cmdline_list)
                if harness_key:
                    # Found an ambient running harness
                    mem_mb = (p.info['memory_info'].rss / (1024 * 1024)) if p.info['memory_info'] else 0.0
                    c_time = datetime.fromtimestamp(p.info['create_time'], timezone.utc).isoformat()
                    external.append(ExternalHarness(
                        harness=harness_key,
                        pid=pid,
                        cwd=p.info['cwd'] or "-",
                        cmdline=" ".join(cmdline_list),
                        cpu_percent=p.info['cpu_percent'] or 0.0,
                        memory_mb=mem_mb,
                        create_time=c_time
                    ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return external

    def discover_ambient_sessions(self, registry=None) -> List[FleetSession]:
        """
        Discover active ambient harness sessions running natively in the workspace
        (e.g. Antigravity CLI, Hermes TUI/daemon, OpenCode service/CLI) that may
        not have been started via 'hmux spawn'.
        """
        import sqlite3
        if registry is None:
            from hmux.registry import HarnessRegistry
            registry = HarnessRegistry()

        ambient_sessions: List[FleetSession] = []
        seen_harnesses = set()

        # 1. Antigravity: Check if running
        is_agy_running = False
        agy_pid = None
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = p.info['name'] or ""
                cmdline = p.info['cmdline'] or []
                if registry.match_process(name, cmdline) == "antigravity":
                    is_agy_running = True
                    agy_pid = p.info['pid']
                    break
            except Exception:
                continue

        if is_agy_running:
            agy_model = registry.resolve_model("antigravity")
            ambient_sessions.append(FleetSession(
                session_id="antigravity-main",
                harness="antigravity",
                task_name="Fleet Orchestration & Pair Programming",
                prompt="",
                worktree_path=os.getcwd(),
                pid=agy_pid or os.getpid(),
                status="RUNNING",
                metadata={"ambient": True},
                model=agy_model
            ))
            seen_harnesses.add("antigravity")

        # 2. Hermes: Check active session lease in ~/.hermes/runtime/active_sessions.json or state.db
        hermes_active_file = Path.home() / ".hermes" / "runtime" / "active_sessions.json"
        hermes_db = Path.home() / ".hermes" / "state.db"
        if hermes_active_file.exists() or hermes_db.exists():
            try:
                hermes_pid = None
                hermes_sid = None
                start_iso = None
                if hermes_active_file.exists():
                    h_data = json.loads(hermes_active_file.read_text())
                    entries = h_data.get("entries", [])
                    if entries:
                        hermes_pid = entries[0].get("pid")
                        hermes_sid = entries[0].get("session_id")
                        ts = entries[0].get("started_at")
                        if ts:
                            start_iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()

                if hermes_db.exists():
                    con = sqlite3.connect(hermes_db)
                    cur = con.cursor()
                    if hermes_sid:
                        cur.execute("SELECT id, title, model, cwd, started_at FROM sessions WHERE id = ? LIMIT 1;", (hermes_sid,))
                        h_row = cur.fetchone()
                    else:
                        h_row = None
                    if not h_row:
                        cur.execute("SELECT id, title, model, cwd, started_at FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1;")
                        h_row = cur.fetchone()
                    con.close()

                    if h_row:
                        native_id = h_row[0]
                        title = h_row[1] or "Hermes Interactive Chat"
                        model = h_row[2] or registry.resolve_model("hermes")
                        cwd = h_row[3] or str(Path.home())
                        if not start_iso and h_row[4]:
                            start_iso = datetime.fromtimestamp(h_row[4], timezone.utc).isoformat()
                        s_id = f"hermes-{native_id[-6:]}" if len(native_id) > 12 else native_id
                        ambient_sessions.append(FleetSession(
                            session_id=s_id,
                            harness="hermes",
                            task_name=title,
                            prompt="",
                            worktree_path=cwd,
                            pid=hermes_pid,
                            status="RUNNING",
                            start_time=start_iso,
                            metadata={"ambient": True, "native_session_id": native_id},
                            model=model
                        ))
                        seen_harnesses.add("hermes")
            except Exception:
                pass

        # 3. OpenCode: only sessions actively executing a turn are ambient-live.
        opencode_db = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        if opencode_db.exists():
            try:
                con = sqlite3.connect(f"file:{opencode_db}?mode=ro", uri=True)
                cur = con.cursor()
                cur.execute(
                    """
                    SELECT id, title, directory, model, time_created
                    FROM session_v2
                    WHERE COALESCE(time_archived, 0) = 0
                      AND parent_id IS NULL
                      AND time_busy IS NOT NULL
                      AND time_busy != 0
                      AND (time_idle IS NULL OR time_busy > time_idle)
                    ORDER BY time_updated DESC
                    LIMIT 25;
                    """
                )
                rows = cur.fetchall()
                con.close()
                if rows:
                    from hmux.opencode import parse_model
                    oc_pid = None
                    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                        try:
                            if registry.match_process(p.info['name'] or '', p.info['cmdline'] or []) == 'opencode':
                                oc_pid = p.info['pid']
                                break
                        except Exception:
                            continue
                    for row in rows:
                        sid = row[0]
                        title = row[1] or "OpenCode Interactive"
                        directory = row[2] or str(Path.home())
                        model = parse_model(row[3]) or registry.resolve_model("opencode")
                        start_iso = datetime.fromtimestamp(row[4] / 1000, timezone.utc).isoformat() if row[4] else None
                        ambient_sessions.append(FleetSession(
                            session_id=sid,
                            harness="opencode",
                            task_name=title,
                            prompt="",
                            worktree_path=directory,
                            pid=oc_pid,
                            status="RUNNING",
                            start_time=start_iso,
                            metadata={"ambient": True, "native_session_id": sid},
                            model=model
                        ))
                    seen_harnesses.add("opencode")
            except Exception:
                pass

        # 4. Any other running harnesses detected via OS processes (e.g. Codex, Pi, Freebuff)
        externals = self.discover_external_harnesses(registry)
        for ext in externals:
            if ext.harness not in seen_harnesses:
                ext_model = registry.resolve_model(ext.harness)
                ambient_sessions.append(FleetSession(
                    session_id=f"{ext.harness}-live",
                    harness=ext.harness,
                    task_name=f"{ext.harness.capitalize()} Process",
                    prompt="",
                    worktree_path=ext.cwd,
                    pid=ext.pid,
                    status="RUNNING",
                    start_time=ext.create_time,
                    metadata={"ambient": True},
                    model=ext_model
                ))
                seen_harnesses.add(ext.harness)

        return ambient_sessions
