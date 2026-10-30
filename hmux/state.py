"""
Persistent Fleet State Manager
Tracks active and historical harness sessions, process health, and workspaces.
"""

import os
import json
import uuid
import time
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

    def is_alive(self) -> bool:
        if not self.pid:
            return False
        # Check if Unix domain socket is active and accepting connections
        if self.socket_path and os.path.exists(self.socket_path):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.3)
                s.connect(self.socket_path)
                s.close()
                return True
            except Exception:
                pass
        try:
            p = psutil.Process(self.pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def update_status(self) -> None:
        """Refresh status based on OS process state."""
        if self.status == "RUNNING":
            if not self.is_alive():
                # Process exited
                self.status = "COMPLETED"
                if not self.end_time:
                    self.end_time = datetime.now(timezone.utc).isoformat()
        elif self.status == "COMPLETED" and self.is_alive():
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

    def list_all(self, refresh: bool = True) -> List[FleetSession]:
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
