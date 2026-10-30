"""
HMUX Fleet Message Router
Enables bi-directional, in-session inter-harness communication without spawning new sessions.
Supports direct messaging, loopback replies, broadcasts, and inbox queues.
"""

import os
import sys
import json
import time
import uuid
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from hmux.state import StateManager, FleetSession

MESSAGES_FILE = Path.home() / ".hmux" / "messages.json"

def format_envelope(from_session: str, message: str) -> str:
    """Format an inter-harness message envelope for terminal injection."""
    border = "─" * 68
    return (
        f"\n\r"
        f"┌── 📨 Inter-Harness Message from [{from_session}] {border[:max(0, 36 - len(from_session))]}\n\r"
        f"│ Message:\n\r"
        f"│   {message.strip()}\n\r"
        f"│\n\r"
        f"│ To report back to {from_session}, run:\n\r"
        f"│   hmux talk {from_session} \"<your response>\"\n\r"
        f"└{'─' * 70}\n\r"
    )

def format_reply_envelope(from_session: str, reply: str) -> str:
    """Format an inter-harness reply envelope."""
    border = "─" * 68
    return (
        f"\n\r"
        f"┌── 📬 Reply from [{from_session}] {border[:max(0, 48 - len(from_session))]}\n\r"
        f"│ {reply.strip()}\n\r"
        f"└{'─' * 70}\n\r"
    )

def format_broadcast_envelope(from_session: str, message: str) -> str:
    """Format a fleet-wide broadcast envelope."""
    border = "─" * 68
    return (
        f"\n\r"
        f"┌── 📢 Fleet Broadcast from [{from_session}] {border[:max(0, 42 - len(from_session))]}\n\r"
        f"│ {message.strip()}\n\r"
        f"└{'─' * 70}\n\r"
    )


class MessageRouter:
    def __init__(self):
        self.state_mgr = StateManager()
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not MESSAGES_FILE.exists():
            self._write_raw({"messages": []})

    def _read_raw(self) -> Dict[str, Any]:
        try:
            with open(MESSAGES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"messages": []}

    def _write_raw(self, data: Dict[str, Any]) -> None:
        temp = MESSAGES_FILE.with_suffix(".tmp")
        with open(temp, "w") as f:
            json.dump(data, f, indent=2)
        temp.replace(MESSAGES_FILE)

    def _inject_socket(self, socket_path: str, payload: str) -> bool:
        """Inject text directly into a running PTY daemon socket."""
        if not os.path.exists(socket_path):
            return False
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(socket_path)
            if not payload.endswith("\n"):
                payload += "\n"
            sock.sendall(payload.encode("utf-8"))
            sock.close()
            return True
        except Exception:
            return False

    def list_active_sessions(self, include_opencode: bool = True) -> List[Dict[str, Any]]:
        """
        List all sessions that can receive messages right now.
        Combines managed sessions and ambient sessions (Hermes, OpenCode, Antigravity, etc.)
        """
        from hmux.registry import HarnessRegistry
        reg = HarnessRegistry()
        sessions = self.state_mgr.list_all(refresh=True)
        ambient_sessions = self.state_mgr.discover_ambient_sessions(reg)

        combined_map: Dict[str, FleetSession] = {}
        for s in sessions:
            if s.is_alive():
                combined_map[s.session_id] = s

        for amb in ambient_sessions:
            if not amb.is_alive():
                continue
            matched_key = None
            for sid, s in combined_map.items():
                if sid == amb.session_id:
                    matched_key = sid
                    break
                amb_nid = amb.metadata.get("native_session_id")
                s_nid = s.metadata.get("native_session_id")
                if amb_nid and s_nid and amb_nid == s_nid:
                    matched_key = sid
                    break

            if matched_key:
                existing = combined_map[matched_key]
                existing.status = "RUNNING"
                if amb.pid:
                    existing.pid = amb.pid
                if amb.model:
                    existing.model = amb.model
                if amb.task_name:
                    existing.task_name = amb.task_name
            else:
                combined_map[amb.session_id] = amb

        active = []
        seen_ids = set()
        seen_native_ids = set()
        for s in combined_map.values():
            if s.status == "RUNNING" and (os.path.exists(s.socket_path) or s.metadata.get("ambient") or s.is_alive()):
                active.append({
                    "session_id": s.session_id,
                    "harness": s.harness,
                    "task_name": s.task_name,
                    "branch": s.branch or "main",
                    "status": s.status,
                    "model": s.model or reg.resolve_model(s.harness),
                    "type": "socket" if os.path.exists(s.socket_path) else "mailbox",
                    "socket_path": s.socket_path
                })
                seen_ids.add(s.session_id)
                native_id = s.metadata.get("native_session_id")
                if native_id:
                    seen_native_ids.add(native_id)

        if include_opencode:
            try:
                from hmux.opencode import OpenCodeDriver
                for item in OpenCodeDriver().list_sessions(limit=25, live_only=True):
                    if item["session_id"] in seen_ids or item["session_id"] in seen_native_ids:
                        continue
                    active.append({
                        "session_id": item["session_id"],
                        "harness": "opencode",
                        "task_name": item["title"],
                        "branch": "main",
                        "status": "RUNNING",
                        "model": item["model"],
                        "type": "opencode-db",
                        "socket_path": "",
                    })
                    seen_ids.add(item["session_id"])
            except Exception:
                pass

        return active

    def register_ambient(self, session_id: str, harness: str, task_name: str, pid: Optional[int] = None) -> FleetSession:
        """Register an ambient / unmanaged harness session so it can send/receive messages."""
        if not pid:
            from hmux.registry import HarnessRegistry
            externals = self.state_mgr.discover_external_harnesses(HarnessRegistry())
            for ext in externals:
                if ext.harness == harness:
                    if harness == "antigravity" or ext.pid > 1:
                        pid = ext.pid
                        break

        from hmux.registry import HarnessRegistry
        reg = HarnessRegistry()
        resolved_model = reg.resolve_model(harness)

        session = FleetSession(
            session_id=session_id,
            harness=harness,
            task_name=task_name,
            prompt="",
            worktree_path=os.getcwd(),
            pid=pid,
            status="RUNNING",
            metadata={"ambient": True},
            model=resolved_model
        )
        self.state_mgr.register(session)
        return session

    def send(
        self,
        to_session: str,
        message: str,
        from_session: str = "ambient",
        wait: bool = False,
        timeout: float = 60.0
    ) -> Tuple[bool, str]:
        """
        Send a message to an existing session without creating a new session.
        If wait=True, blocks until the target session reports back or timeout expires.
        """
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        # Check if this is a reply to an in-flight message waiting for a response
        data = self._read_raw()
        matched_in_flight = None
        for item in reversed(data.get("messages", [])):
            if (
                item.get("to_session") == from_session
                and item.get("from_session") == to_session
                and item.get("status") in ("SENT", "DELIVERED")
            ):
                matched_in_flight = item
                break

        if matched_in_flight:
            # Mark the original message as replied
            matched_in_flight["status"] = "REPLIED"
            matched_in_flight["reply"] = message
            matched_in_flight["reply_timestamp"] = now
            self._write_raw(data)

            # Also deliver to the original sender's socket if active
            target_session = self.state_mgr.get(to_session)
            if target_session and target_session.status == "RUNNING":
                self._inject_socket(
                    target_session.socket_path,
                    format_reply_envelope(from_session, message)
                )

            return True, f"Delivered reply to {to_session}"

        # Standard new message record
        record = {
            "id": msg_id,
            "from_session": from_session,
            "to_session": to_session,
            "body": message,
            "timestamp": now,
            "status": "SENT",
            "reply": None,
            "reply_timestamp": None
        }

        # Check target session
        target = self.state_mgr.get(to_session)
        if not target:
            # 1. Match against existing managed sessions by native_session_id or prefix
            for s in self.state_mgr.list_all(refresh=False):
                if s.metadata.get("native_session_id") == to_session or s.session_id == to_session:
                    target = s
                    break

        if not target:
            # 2. Check ambient sessions discovered across system
            from hmux.registry import HarnessRegistry
            reg = HarnessRegistry()
            ambient_sessions = self.state_mgr.discover_ambient_sessions(reg)
            clean_target = to_session.lower().strip()
            for amb in ambient_sessions:
                amb_nid = amb.metadata.get("native_session_id")
                if (
                    amb.session_id == to_session
                    or (amb_nid and amb_nid == to_session)
                    or (amb_nid and amb_nid.endswith(to_session.replace("hermes-", "")))
                    or amb.harness == clean_target
                    or (clean_target in ("hermes", "hermes-live") and amb.harness == "hermes")
                    or (clean_target in ("opencode", "opencode-live") and amb.harness == "opencode")
                    or (clean_target in ("antigravity", "antigravity-main") and amb.harness == "antigravity")
                ):
                    target = amb
                    try:
                        self.state_mgr.save(target)
                    except Exception:
                        pass
                    break

        if not target and (to_session.startswith("ses_") or to_session == "opencode"):
            from hmux.opencode import OpenCodeDriver
            from hmux.registry import HarnessRegistry
            driver = OpenCodeDriver()
            info = driver.get_session_info(to_session) if to_session.startswith("ses_") else None
            if not info:
                sessions = driver.list_sessions(limit=1, live_only=True)
                if sessions:
                    info = driver.get_session_info(sessions[0]["session_id"])
            if info:
                model_name = info["model"] or HarnessRegistry().resolve_model("opencode")
                target = FleetSession(
                    session_id=info["session_id"],
                    harness="opencode",
                    task_name=info["title"] or "OpenCode Chat",
                    prompt="",
                    worktree_path=info["directory"] or os.getcwd(),
                    status="RUNNING",
                    metadata={
                        "ambient": True,
                        "native_session_id": info["session_id"],
                        "opencode_db": True,
                    },
                    model=model_name
                )
                self.state_mgr.register(target)

        if not target and (to_session.startswith("local-") or to_session.startswith("hermes")):
            from hmux.hermes import HermesDriver
            from hmux.registry import HarnessRegistry
            driver = HermesDriver()
            db_path = driver.get_db_path()
            if db_path.exists():
                try:
                    import sqlite3
                    conn = sqlite3.connect(db_path)
                    c = conn.cursor()
                    if to_session.startswith("local-"):
                        c.execute("SELECT id, title, model, cwd FROM sessions WHERE id = ? LIMIT 1;", (to_session,))
                        row = c.fetchone()
                    elif to_session.startswith("hermes-"):
                        suffix = to_session[len("hermes-"):]
                        c.execute("SELECT id, title, model, cwd FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1;", (f"%{suffix}",))
                        row = c.fetchone()
                    else:
                        try:
                            c.execute("SELECT id, title, model, cwd FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1;")
                            row = c.fetchone()
                        except Exception:
                            row = None
                        if not row:
                            c.execute("SELECT id, title, model, cwd FROM sessions ORDER BY started_at DESC LIMIT 1;")
                            row = c.fetchone()
                    conn.close()
                    if row:
                        from hmux.registry import HarnessRegistry
                        model_name = row[2] or driver.get_session_model(row[0]) or HarnessRegistry().resolve_model("hermes")
                        s_id = to_session if (to_session.startswith("local-") or to_session.startswith("hermes-")) else (f"hermes-{row[0][-6:]}" if len(row[0]) > 12 else row[0])
                        target = FleetSession(
                            session_id=s_id,
                            harness="hermes",
                            task_name=row[1] or "Hermes Chat",
                            prompt="",
                            worktree_path=row[3] or os.getcwd(),
                            status="RUNNING",
                            metadata={"ambient": True, "native_session_id": row[0]},
                            model=model_name
                        )
                        self.state_mgr.register(target)
                except Exception:
                    pass

        delivered_socket = False
        if target:
            if target.harness == "freebuff" and target.status == "RUNNING":
                from hmux.freebuff import FreebuffDriver
                driver = FreebuffDriver()
                if wait:
                    reply = driver.converse(target, message, timeout=timeout)
                    if reply:
                        record["status"] = "REPLIED"
                        record["reply"] = reply
                        record["reply_timestamp"] = datetime.now(timezone.utc).isoformat()
                        data["messages"].append(record)
                        self._write_raw(data)
                        return True, reply
                    else:
                        record["status"] = "TIMEOUT"
                        data["messages"].append(record)
                        self._write_raw(data)
                        return False, f"Timeout waiting for reply from Freebuff session {to_session} ({timeout}s elapsed)"
                else:
                    delivered_socket = driver.send_prompt(target, message)
                    if delivered_socket:
                        record["status"] = "DELIVERED"
            elif target.harness == "opencode" and not (
                target.status == "RUNNING" and os.path.exists(target.socket_path)
            ):
                from hmux.opencode import OpenCodeDriver
                driver = OpenCodeDriver()
                if wait:
                    reply = driver.converse(target, message, timeout=timeout)
                    if reply:
                        record["status"] = "REPLIED"
                        record["reply"] = reply
                        record["reply_timestamp"] = datetime.now(timezone.utc).isoformat()
                        data["messages"].append(record)
                        self._write_raw(data)
                        return True, reply
                    else:
                        record["status"] = "TIMEOUT"
                        data["messages"].append(record)
                        self._write_raw(data)
                        return False, f"Timeout waiting for reply from OpenCode session {to_session} ({timeout}s elapsed)"
                else:
                    delivered_socket = driver.send_prompt(target, message)
                    if delivered_socket:
                        record["status"] = "DELIVERED"
            elif target.harness == "hermes":
                from hmux.hermes import HermesDriver
                driver = HermesDriver()
                if wait:
                    reply = driver.converse(target, message, timeout=timeout)
                    if reply:
                        record["status"] = "REPLIED"
                        record["reply"] = reply
                        record["reply_timestamp"] = datetime.now(timezone.utc).isoformat()
                        data["messages"].append(record)
                        self._write_raw(data)
                        return True, reply
                    else:
                        record["status"] = "TIMEOUT"
                        data["messages"].append(record)
                        self._write_raw(data)
                        return False, f"Timeout waiting for reply from Hermes session {to_session} ({timeout}s elapsed)"
                else:
                    delivered_socket = driver.send_prompt(target, message)
                    if delivered_socket:
                        record["status"] = "DELIVERED"
            elif target.status == "RUNNING":
                delivered_socket = self._inject_socket(
                    target.socket_path,
                    format_envelope(from_session, message)
                )
                if delivered_socket:
                    record["status"] = "DELIVERED"

        data["messages"].append(record)
        # Keep last 500 messages to prevent unbounded growth
        if len(data["messages"]) > 500:
            data["messages"] = data["messages"][-500:]
        self._write_raw(data)

        if not delivered_socket and record.get("status") != "DELIVERED" and (not target or target.status != "RUNNING"):
            if target and target.metadata.get("ambient"):
                record["status"] = "DELIVERED"
                self._write_raw(data)
                if not wait:
                    return True, f"Message {msg_id} delivered to mailbox of {to_session}"
            else:
                return False, f"Session '{to_session}' is not active or running in HMUX."

        if not wait:
            return True, f"Message {msg_id} delivered to {to_session}"

        # Wait loop for reply
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            time.sleep(0.3)
            current_data = self._read_raw()
            for item in current_data.get("messages", []):
                if item.get("id") == msg_id and item.get("status") == "REPLIED":
                    reply_text = item.get("reply", "")
                    return True, reply_text

        return False, f"Timeout waiting for reply from {to_session} ({timeout}s elapsed)"

    def broadcast(self, message: str, from_session: str = "ambient") -> Dict[str, Any]:
        """Broadcast a message to all active sessions in the fleet."""
        active = self.list_active_sessions()
        delivered = []
        envelope = format_broadcast_envelope(from_session, message)

        for s in active:
            if s["session_id"] != from_session:
                if self._inject_socket(s["socket_path"], envelope):
                    delivered.append(s["session_id"])

        # Record in message log
        data = self._read_raw()
        data["messages"].append({
            "id": f"bcast-{uuid.uuid4().hex[:8]}",
            "from_session": from_session,
            "to_session": "ALL",
            "body": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "BROADCAST",
            "recipients": delivered
        })
        self._write_raw(data)

        return {
            "delivered_count": len(delivered),
            "recipients": delivered
        }

    def get_inbox(self, session_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve recent messages sent to or from a given session."""
        data = self._read_raw()
        all_msgs = data.get("messages", [])
        if not session_id:
            return all_msgs[-limit:]

        filtered = [
            m for m in all_msgs
            if m.get("to_session") in (session_id, "ALL") or m.get("from_session") == session_id
        ]
        return filtered[-limit:]
