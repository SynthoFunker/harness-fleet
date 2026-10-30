"""
OpenCode Driver & Multi-Turn Conversation Engine
Enables programmatic interaction, prompt injection, and response extraction
for the OpenCode coding agent within HMUX.
"""

import os
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from hmux.state import FleetSession


class OpenCodeDriver:
    """
    Manages automated interactions, multi-turn session continuation,
    and response extraction for OpenCode.
    """

    @staticmethod
    def get_db_path() -> Path:
        return Path.home() / ".local" / "share" / "opencode" / "opencode.db"

    @staticmethod
    def get_binary_path() -> str:
        local_bin = Path.home() / ".opencode" / "bin" / "opencode"
        if local_bin.exists():
            return str(local_bin)
        return "opencode"

    def resolve_native_session_id(self, session: FleetSession) -> Optional[str]:
        """
        Resolve the OpenCode SQLite session ID (ses_...) corresponding
        to an HMUX FleetSession.
        """
        if session.metadata.get("native_session_id"):
            return session.metadata["native_session_id"]

        if session.session_id.startswith("ses_"):
            return session.session_id

        db_path = self.get_db_path()
        if not db_path.exists():
            return None

        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()

            # 1. Match by session prompt in session_message table
            if session.prompt:
                search_snippet = session.prompt.strip()[:40]
                c.execute(
                    "SELECT session_id FROM session_message WHERE data LIKE ? ORDER BY time_created DESC LIMIT 1;",
                    (f"%{search_snippet}%",)
                )
                row = c.fetchone()
                if row:
                    conn.close()
                    return row[0]

            # 2. Match by worktree path in session_v2
            c.execute(
                "SELECT id FROM session_v2 WHERE directory = ? ORDER BY time_updated DESC LIMIT 1;",
                (session.worktree_path,)
            )
            row = c.fetchone()
            conn.close()
            if row:
                return row[0]

        except Exception:
            pass

        return None

    def get_latest_message_id(self, native_session_id: str) -> Optional[str]:
        """Get the ID of the most recent message in the session."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute(
                "SELECT id FROM session_message WHERE session_id = ? ORDER BY time_created DESC LIMIT 1;",
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
        initial_msg_id: Optional[str] = None
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
                "SELECT id, data FROM session_message WHERE session_id = ? AND type = 'assistant' ORDER BY time_created DESC LIMIT 1;",
                (native_session_id,)
            )
            row = c.fetchone()
            conn.close()
            if not row:
                return None

            msg_id, raw_data = row
            if initial_msg_id and msg_id == initial_msg_id:
                # No new message generated yet
                return None

            data = json.loads(raw_data)
            text_blocks = []
            for item in data.get("content", []):
                if item.get("type") == "text" and item.get("text"):
                    text_blocks.append(item["text"])

            if text_blocks:
                return "\n\n".join(text_blocks)
            elif data.get("text"):
                return data["text"]

        except Exception:
            pass

        return None

    def get_session_model(self, native_session_id: str) -> Optional[str]:
        """Dynamically retrieve the model configured for a native OpenCode session."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT model FROM session_v2 WHERE id = ? LIMIT 1;", (native_session_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                raw = row[0]
                if raw.startswith("{"):
                    d = json.loads(raw)
                    prov = d.get("providerID")
                    mid = d.get("id")
                    if mid:
                        return f"{prov}/{mid}" if prov and not mid.startswith(f"{prov}/") else mid
                return raw
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
        Send a message into the OpenCode session and return the assistant reply.
        """
        native_id = self.resolve_native_session_id(session)
        initial_msg_id = self.get_latest_message_id(native_id) if native_id else None

        # Dynamically determine model from session, native db, or system registry
        model = session.model
        if not model and native_id:
            model = self.get_session_model(native_id)
        if not model:
            from hmux.registry import HarnessRegistry
            model = HarnessRegistry().resolve_model("opencode")

        bin_path = self.get_binary_path()
        cmd = [bin_path, "run", "--standalone"]
        if model:
            cmd.extend(["-m", model])
        if native_id:
            cmd.extend(["-s", native_id])
        cmd.append(prompt)

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

            # Extract structured response from opencode.db
            if native_id:
                reply = self.extract_latest_response(native_id, initial_msg_id=initial_msg_id)
                if reply:
                    return reply

            # Fallback to stdout if DB extraction did not yield content
            if res.stdout and res.stdout.strip():
                return res.stdout.strip()

        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

        return None

    def send_prompt(self, session: FleetSession, prompt: str) -> bool:
        """
        Deliver a prompt to OpenCode asynchronously.
        """
        import threading
        t = threading.Thread(target=self.converse, args=(session, prompt, 60.0), daemon=True)
        t.start()
        return True
