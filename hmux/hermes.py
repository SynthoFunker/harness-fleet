"""
Hermes Driver & Multi-Turn Conversation Engine
Enables programmatic interaction, prompt injection, session resumption,
and response extraction for the Hermes agent within HMUX.
"""

import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from hmux.state import FleetSession


class HermesDriver:
    """
    Manages automated interactions, multi-turn session continuation,
    and response extraction for Hermes Agent.
    """

    @staticmethod
    def get_db_path() -> Path:
        return Path.home() / ".hermes" / "state.db"

    @staticmethod
    def get_binary_path() -> str:
        # Check standard installation locations
        custom_bin = Path.home() / ".hermes" / "hermes-agent" / ".hermes" / "bin" / "hermes"
        if custom_bin.exists() and os.access(custom_bin, os.X_OK):
            return str(custom_bin)
        local_bin = Path.home() / ".local" / "bin" / "hermes"
        if local_bin.exists() and os.access(local_bin, os.X_OK):
            return str(local_bin)
        which_bin = shutil.which("hermes")
        if which_bin:
            return which_bin
        return "hermes"

    def resolve_native_session_id(self, session: FleetSession) -> Optional[str]:
        """
        Resolve the Hermes SQLite session ID (local-...) corresponding
        to an HMUX FleetSession.
        """
        # 1. Direct metadata check
        if session.metadata.get("native_session_id"):
            return session.metadata["native_session_id"]

        # 2. Check if FleetSession ID is already a native Hermes ID
        if session.session_id.startswith("local-"):
            return session.session_id

        # 3. Check session log file for printed session_id
        if session.log_file and os.path.exists(session.log_file):
            try:
                with open(session.log_file, "r", encoding="utf-8", errors="ignore") as f:
                    log_content = f.read()
                    m = re.search(r"session_id:\s*(local-[a-f0-9]+)", log_content)
                    if m:
                        return m.group(1)
            except Exception:
                pass

        # 4. Check Hermes SQLite database
        db_path = self.get_db_path()
        if not db_path.exists():
            return None

        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()

            # Match by session prompt in messages table
            if session.prompt:
                search_snippet = session.prompt.strip()[:40]
                c.execute(
                    "SELECT session_id FROM messages WHERE role = 'user' AND content LIKE ? ORDER BY id DESC LIMIT 1;",
                    (f"%{search_snippet}%",)
                )
                row = c.fetchone()
                if row:
                    conn.close()
                    return row[0]

            # Match by worktree path in sessions table
            if session.worktree_path:
                c.execute(
                    "SELECT id FROM sessions WHERE cwd = ? ORDER BY started_at DESC LIMIT 1;",
                    (session.worktree_path,)
                )
                row = c.fetchone()
                conn.close()
                if row:
                    return row[0]

        except Exception:
            pass

        return None

    def get_latest_message_id(self, native_session_id: str) -> Optional[int]:
        """Get the integer ID of the most recent message in the session."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute(
                "SELECT id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 1;",
                (native_session_id,)
            )
            row = c.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def extract_latest_response(
        self,
        native_session_id: str,
        initial_msg_id: Optional[int] = None
    ) -> Optional[str]:
        """
        Extract the completed text response of the latest assistant message.
        """
        db_path = self.get_db_path()
        if not db_path.exists():
            return None

        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute(
                "SELECT id, content FROM messages WHERE session_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1;",
                (native_session_id,)
            )
            row = c.fetchone()
            conn.close()
            if not row:
                return None

            msg_id, content = row
            if initial_msg_id is not None and msg_id <= initial_msg_id:
                # No new assistant turn was recorded
                return None

            if content and content.strip():
                return content.strip()

        except Exception:
            pass

        return None

    def get_session_model(self, native_session_id: str) -> Optional[str]:
        """Dynamically retrieve the model configured for a native Hermes session."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT model FROM sessions WHERE id = ? LIMIT 1;", (native_session_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
        return None

    def converse(
        self,
        session: FleetSession,
        prompt: str,
        timeout: float = 60.0
    ) -> Optional[str]:
        """
        Send a message into the Hermes session and return the assistant reply.
        """
        native_id = self.resolve_native_session_id(session)
        initial_msg_id = self.get_latest_message_id(native_id) if native_id else None

        # Dynamically determine model from session, native db, or system registry
        model = session.model
        if not model and native_id:
            model = self.get_session_model(native_id)
        if not model:
            from hmux.registry import HarnessRegistry
            model = HarnessRegistry().resolve_model("hermes")

        bin_path = self.get_binary_path()
        cmd = [bin_path, "chat", "-q", prompt, "-Q", "--oneshot", "--yolo"]
        if native_id:
            cmd.extend(["--resume", native_id])
        elif model:
            cmd.extend(["-m", model])
        if session.worktree_path:
            cmd.extend(["--in", session.worktree_path, "--no-restore-cwd"])

        try:
            res = subprocess.run(
                cmd,
                cwd=session.worktree_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            # Append output to session logs
            if session.log_file:
                if isinstance(res.stdout, str) and res.stdout:
                    with open(session.log_file, "a", encoding="utf-8") as f:
                        f.write(res.stdout + "\n")
                if isinstance(res.stderr, str) and res.stderr:
                    with open(session.log_file + ".err", "a", encoding="utf-8") as f:
                        f.write(res.stderr + "\n")

            # Discover native ID if this was a new session
            if not native_id:
                native_id = self.resolve_native_session_id(session)
                if native_id:
                    session.metadata["native_session_id"] = native_id

            # Extract structured response from state.db
            if native_id:
                reply = self.extract_latest_response(native_id, initial_msg_id=initial_msg_id)
                if reply:
                    return reply

            # Fallback to stdout if DB extraction did not yield content
            if res.stdout and res.stdout.strip():
                # Filter out session_id header line and resume banner if present
                lines = [
                    l for l in res.stdout.strip().splitlines()
                    if not l.startswith("session_id:") and not l.startswith("↻ Resumed session")
                ]
                cleaned = "\n".join(lines).strip()
                if cleaned:
                    return cleaned

        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

        return None

    def send_prompt(self, session: FleetSession, prompt: str) -> bool:
        """
        Deliver a prompt to Hermes asynchronously.
        """
        import threading
        t = threading.Thread(target=self.converse, args=(session, prompt, 60.0), daemon=True)
        t.start()
        return True
