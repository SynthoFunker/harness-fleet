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

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions that can receive messages right now."""
        sessions = self.state_mgr.list_all(refresh=True)
        active = []
        for s in sessions:
            if s.status == "RUNNING" and (os.path.exists(s.socket_path) or s.metadata.get("ambient")):
                active.append({
                    "session_id": s.session_id,
                    "harness": s.harness,
                    "task_name": s.task_name,
                    "branch": s.branch or "main",
                    "status": s.status,
                    "model": s.model,
                    "type": "socket" if os.path.exists(s.socket_path) else "mailbox",
                    "socket_path": s.socket_path
                })
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
        delivered_socket = False
        if target and target.status == "RUNNING":
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

        if not delivered_socket and (not target or target.status != "RUNNING"):
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
