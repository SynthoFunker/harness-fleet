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


def parse_model(raw: Any) -> Optional[str]:
    """Normalize the many shapes OpenCode stores a model reference in."""
    if not raw:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        if raw.startswith("{"):
            try:
                raw = json.loads(raw)
            except Exception:
                return raw
        else:
            return raw
    if isinstance(raw, dict):
        provider = raw.get("providerID") or raw.get("provider")
        model = raw.get("id") or raw.get("modelID") or raw.get("model")
        if not model:
            return None
        model = str(model)
        if provider and "/" not in model:
            return f"{provider}/{model}"
        return model
    return str(raw)


def extract_message_text(raw_data: Any) -> str:
    """Extract only the visible text blocks from an OpenCode message payload."""
    if not raw_data:
        return ""
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except Exception:
            return raw_data.strip()
    elif isinstance(raw_data, dict):
        data = raw_data
    else:
        return ""

    blocks: List[str] = []

    content = data.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                blocks.append(str(item["text"]))

    # Some OpenCode versions keep text in a separate parts collection.
    if not blocks:
        parts = data.get("parts")
        if isinstance(parts, list):
            for item in parts:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    blocks.append(str(item["text"]))

    if not blocks and isinstance(data.get("text"), str) and data["text"].strip():
        blocks.append(data["text"])

    return "\n\n".join(block for block in blocks if block).strip()


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

    @staticmethod
    def _connect(db_path: Path) -> sqlite3.Connection:
        """Open the live OpenCode database without taking write locks."""
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)

    @staticmethod
    def _message_order_column(conn: sqlite3.Connection) -> str:
        """Use `seq` on modern OpenCode stores, `time_created` on older/minimal ones."""
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(session_message);")
            }
        except Exception:
            columns = set()
        return "seq" if "seq" in columns else "time_created"

    def session_exists(self, native_session_id: str) -> bool:
        return self.get_session_info(native_session_id) is not None

    def get_session_info(self, native_session_id: str) -> Optional[Dict[str, Any]]:
        """Read a session's title, workspace, model, and activity from opencode.db."""
        if not native_session_id:
            return None
        db_path = self.get_db_path()
        if not db_path.exists():
            return None

        try:
            conn = self._connect(db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                """
                SELECT id, title, directory, model, time_created, time_updated,
                       time_busy, time_idle, parent_id, time_archived, summary_diffs
                FROM session_v2
                WHERE id = ? LIMIT 1;
                """,
                (native_session_id,),
            )
            row = c.fetchone()
            conn.close()
        except Exception:
            return None

        if not row:
            return None

        title = row["title"] or self.get_first_user_text(native_session_id) or "OpenCode Session"
        busy = bool(row["time_busy"]) and (
            not row["time_idle"] or row["time_busy"] > row["time_idle"]
        )
        return {
            "session_id": row["id"],
            "title": title,
            "directory": row["directory"] or os.getcwd(),
            "model": parse_model(row["model"]),
            "time_created": row["time_created"],
            "time_updated": row["time_updated"],
            "busy": busy,
            "parent_id": row["parent_id"],
            "archived": bool(row["time_archived"]),
            "summary_diffs": row["summary_diffs"],
        }

    def get_first_user_text(self, native_session_id: str) -> str:
        """Use the first real user prompt as a title when OpenCode has none yet."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return ""
        try:
            conn = self._connect(db_path)
            order_col = self._message_order_column(conn)
            c = conn.cursor()
            c.execute(
                f"""
                SELECT data FROM session_message
                WHERE session_id = ? AND type = 'user'
                ORDER BY {order_col} ASC LIMIT 1;
                """,
                (native_session_id,),
            )
            row = c.fetchone()
            conn.close()
            if row:
                text = extract_message_text(row[0])
                return text.splitlines()[0][:120] if text else ""
        except Exception:
            pass
        return ""

    def list_sessions(self, limit: int = 25, live_only: bool = False) -> List[Dict[str, Any]]:
        """List OpenCode sessions, newest first, including their real titles and models."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return []

        try:
            conn = self._connect(db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                """
                SELECT id, title, directory, model, time_created, time_updated,
                       time_busy, time_idle
                FROM session_v2
                WHERE parent_id IS NULL
                  AND COALESCE(time_archived, 0) = 0
                ORDER BY time_updated DESC
                LIMIT ?;
                """,
                (max(1, limit),),
            )
            rows = c.fetchall()
            conn.close()
        except Exception:
            return []

        sessions: List[Dict[str, Any]] = []
        for row in rows:
            busy = bool(row["time_busy"]) and (
                not row["time_idle"] or row["time_busy"] > row["time_idle"]
            )
            sessions.append({
                "session_id": row["id"],
                "title": row["title"] or self.get_first_user_text(row["id"]) or "OpenCode Session",
                "directory": row["directory"] or os.getcwd(),
                "model": parse_model(row["model"]),
                "time_created": row["time_created"],
                "time_updated": row["time_updated"],
                "busy": busy,
                # "Live" means the session is actively executing a turn. Idle
                # sessions remain addressable through the driver, they just do
                # not claim a slot in the live fleet view.
                "is_live": busy,
                "status": "BUSY" if busy else "IDLE",
            })

        if live_only:
            sessions = [s for s in sessions if s["is_live"]]
        return sessions

    def get_session_title(self, native_session_id: str) -> Optional[str]:
        info = self.get_session_info(native_session_id)
        return info["title"] if info else None

    def get_latest_seq(self, native_session_id: str) -> Optional[int]:
        """Highest durable message sequence number, used as a turn watermark."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return None
        try:
            conn = self._connect(db_path)
            order_col = self._message_order_column(conn)
            if order_col != "seq":
                conn.close()
                return None
            c = conn.cursor()
            c.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM session_message WHERE session_id = ?;",
                (native_session_id,),
            )
            row = c.fetchone()
            conn.close()
            return int(row[0]) if row else None
        except Exception:
            return None

    def get_latest_message_id(self, native_session_id: str) -> Optional[str]:
        """Get the ID of the most recent message in the session."""
        db_path = self.get_db_path()
        if not db_path.exists():
            return None
        try:
            conn = self._connect(db_path)
            order_col = self._message_order_column(conn)
            c = conn.cursor()
            c.execute(
                f"SELECT id FROM session_message WHERE session_id = ? ORDER BY {order_col} DESC LIMIT 1;",
                (native_session_id,),
            )
            row = c.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def extract_latest_response(
        self,
        native_session_id: str,
        initial_msg_id: Optional[str] = None,
        initial_seq: Optional[int] = None,
    ) -> Optional[str]:
        """
        Extract the completed text response of the newest assistant message.

        ``initial_seq`` is the safe watermark: assistant messages at or below it
        belong to a previous turn and must never be returned as a fresh reply.
        """
        db_path = self.get_db_path()
        if not db_path.exists():
            return None

        try:
            conn = self._connect(db_path)
            order_col = self._message_order_column(conn)
            c = conn.cursor()
            query = (
                "SELECT id, data FROM session_message "
                "WHERE session_id = ? AND type = 'assistant'"
            )
            params: List[Any] = [native_session_id]
            if initial_seq is not None and order_col == "seq":
                query += " AND seq > ?"
                params.append(initial_seq)
            query += f" ORDER BY {order_col} DESC LIMIT 1;"
            c.execute(query, params)
            row = c.fetchone()
            conn.close()
            if not row:
                return None

            msg_id, raw_data = row
            if initial_msg_id and msg_id == initial_msg_id:
                return None
            return extract_message_text(raw_data) or None
        except Exception:
            return None

    def get_session_model(self, native_session_id: str) -> Optional[str]:
        """Dynamically retrieve the model configured for a native OpenCode session."""
        db_path = self.get_db_path()
        if db_path.exists():
            try:
                conn = self._connect(db_path)
                row = conn.execute(
                    "SELECT model FROM session_v2 WHERE id = ? LIMIT 1;",
                    (native_session_id,),
                ).fetchone()
                conn.close()
                model = parse_model(row[0]) if row else None
                if model:
                    return model
            except Exception:
                pass
        info = self.get_session_info(native_session_id)
        return info["model"] if info else None

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
            conn = self._connect(db_path)
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

    @staticmethod
    def _parse_json_events(stdout: str) -> Dict[str, Any]:
        """Parse `opencode run --format json` JSONL into a session id and text."""
        session_id = None
        text_blocks: List[str] = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not session_id and event.get("sessionID"):
                session_id = event["sessionID"]
            part = event.get("part") or {}
            if event.get("type") == "text" and part.get("text"):
                text_blocks.append(str(part["text"]))
        return {
            "session_id": session_id,
            "text": "\n\n".join(text_blocks).strip(),
        }

    def converse(
        self,
        session: FleetSession,
        prompt: str,
        timeout: float = 60.0
    ) -> Optional[str]:
        """
        Send a message into the OpenCode session and return the assistant reply.

        The command intentionally does not use ``--standalone``: it connects to
        the shared OpenCode service, so a prompt sent from another harness lands
        in the live TUI session instead of a private parallel server.
        """
        native_id = self.resolve_native_session_id(session)
        initial_seq = self.get_latest_seq(native_id) if native_id else None
        initial_msg_id = self.get_latest_message_id(native_id) if native_id else None

        # Dynamically determine model from session, native db, or system registry
        model = session.model
        if not model and native_id:
            model = self.get_session_model(native_id)
        if not model:
            from hmux.registry import HarnessRegistry
            model = HarnessRegistry().resolve_model("opencode")

        bin_path = self.get_binary_path()
        cmd = [bin_path, "run", "--format", "json"]
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

            parsed = self._parse_json_events(res.stdout or "")

            # Discover native ID if this was a new session
            if not native_id:
                native_id = parsed.get("session_id") or self.resolve_native_session_id(session)
                if native_id:
                    session.metadata["native_session_id"] = native_id

            # Extract structured response from opencode.db
            if native_id:
                reply = self.extract_latest_response(
                    native_id,
                    initial_msg_id=initial_msg_id,
                    initial_seq=initial_seq,
                )
                if reply:
                    return reply

            # Fallback to the JSON event stream if DB extraction did not yield content
            if parsed.get("text"):
                return parsed["text"]

            # Last resort: plain stdout for older OpenCode versions
            if res.returncode == 0 and res.stdout and res.stdout.strip():
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
