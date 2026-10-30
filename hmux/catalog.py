"""
HMUX Universal Session Catalog
Scans and parses native sessions across all harnesses (Antigravity, Codex, Pi,
Hermes, Freebuff, and OpenCode) to extract real conversation titles, goals,
touched files, and metadata.
"""

import os
import sys
import json
import ast
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

# Add session-transmuter to path if present
TRANSMUTER_DIR = Path.home() / "Projects" / "session-transmuter"
if TRANSMUTER_DIR.exists() and str(TRANSMUTER_DIR) not in sys.path:
    sys.path.insert(0, str(TRANSMUTER_DIR))

from hmux.state import StateManager

def clean_session_title(raw: Any) -> str:
    """Extract a human-readable title from potentially complex payload objects."""
    if not raw:
        return "Untitled Task"
    if isinstance(raw, list) and raw:
        if isinstance(raw[0], dict) and "text" in raw[0]:
            return clean_session_title(raw[0]["text"])
        return clean_session_title(raw[0])
    if isinstance(raw, dict) and "text" in raw:
        return clean_session_title(raw["text"])
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("[") and ("input_text" in raw or "text" in raw):
            try:
                parsed = ast.literal_eval(raw)
                return clean_session_title(parsed)
            except Exception:
                pass
        # Remove XML tags like <USER_REQUEST>
        if "<USER_REQUEST>" in raw:
            parts = raw.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")
            if parts and parts[0].strip():
                return parts[0].strip()[:100]
        # Skip injected AGENTS.md headers
        if "# AGENTS.md instructions" in raw or "<INSTRUCTIONS>" in raw:
            parts = raw.split("</INSTRUCTIONS>")
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()[:100]
        # Truncate
        return raw[:100]
    return str(raw)[:100]


class SessionCatalog:
    def __init__(self):
        self.state_mgr = StateManager()

    @staticmethod
    def _format_timestamp(milliseconds: Optional[int]) -> str:
        if not milliseconds:
            return "-"
        try:
            return datetime.fromtimestamp(
                milliseconds / 1000, timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "-"

    @staticmethod
    def _parse_opencode_summary_files(raw: Any) -> List[str]:
        """Extract file paths from the session's summary_diffs payload."""
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return []

        files: List[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    files.append(item)
                elif isinstance(item, dict):
                    path = item.get("file") or item.get("path") or item.get("filename")
                    if path:
                        files.append(str(path))
        elif isinstance(data, dict):
            for key in ("files", "paths"):
                value = data.get(key)
                if isinstance(value, list):
                    files.extend(str(v) for v in value)
            if not files:
                files.extend(
                    str(key) for key in data.keys()
                    if isinstance(key, str) and ("/" in key or "." in key)
                )
        return list(dict.fromkeys(files))[:20]

    def list_all_sessions(
        self,
        harness_filter: Optional[str] = None,
        limit: int = 35
    ) -> List[Dict[str, Any]]:
        """
        Discovers historical and active sessions across all supported harnesses,
        returning their session IDs, real titles, and status.
        """
        entries: List[Dict[str, Any]] = []

        # 1. Sessions exposed by session-transmuter (Antigravity, Codex, Pi,
        #    Hermes, Freebuff). OpenCode is handled natively below because the
        #    installed transmuter does not discover it.
        if harness_filter != "opencode":
            try:
                from transmuter import (
                    discover_all_sessions,
                    AntigravityAdapter,
                    CodexAdapter,
                    PiAdapter,
                    HermesAdapter
                )
                discover = discover_all_sessions
            except ImportError:
                discover = None

            if discover:
                all_raw = discover()
                if harness_filter:
                    all_raw = [s for s in all_raw if s["harness"] == harness_filter]

                # Find live sessions to cross-reference
                live_sessions = {
                    s.session_id: s
                    for s in self.state_mgr.list_all(refresh=True)
                    if s.status == "RUNNING"
                }

                for item in all_raw[:max(limit, 20)]:
                    h = item["harness"]
                    sid = item["id"]
                    p = Path(item["path"])
                    try:
                        mtime = p.stat().st_mtime if p.exists() else 0
                    except Exception:
                        mtime = 0
                    dt_str = datetime.fromtimestamp(
                        mtime, timezone.utc
                    ).strftime("%Y-%m-%d %H:%M") if mtime else "-"

                    title = "No title"
                    try:
                        if h == "antigravity":
                            sess = AntigravityAdapter.parse(p)
                            title = clean_session_title(sess.title)
                        elif h == "codex":
                            sess = CodexAdapter.parse(p)
                            # Find first real user prompt that isn't instructions
                            chosen_title = ""
                            for turn in sess.turns:
                                if turn.get("role") == "user":
                                    t_text = clean_session_title(turn.get("text", ""))
                                    if t_text and "AGENTS.md" not in t_text:
                                        chosen_title = t_text
                                        break
                            title = chosen_title or clean_session_title(sess.title)
                        elif h == "pi":
                            sess = PiAdapter.parse(p)
                            title = clean_session_title(sess.title)
                        elif h == "hermes":
                            title = item.get("name", "Hermes Session")
                        elif h == "freebuff":
                            title = f"Chat {sid.split(':')[-1] if ':' in sid else sid}"
                    except Exception:
                        title = "Session (unparsed)"

                    is_live = sid in live_sessions or any(
                        sid in s_id for s_id in live_sessions
                    )

                    entries.append({
                        "harness": h,
                        "session_id": sid,
                        "title": title or "Interactive Session",
                        "updated_at": dt_str,
                        "path": str(p),
                        "is_live": is_live,
                        "_ts": mtime,
                    })

        # 2. Native OpenCode sessions from the SQLite store.
        if not harness_filter or harness_filter == "opencode":
            try:
                from hmux.opencode import OpenCodeDriver
                driver = OpenCodeDriver()
                for item in driver.list_sessions(limit=max(limit, 20)):
                    entries.append({
                        "harness": "opencode",
                        "session_id": item["session_id"],
                        "title": item["title"],
                        "updated_at": self._format_timestamp(item["time_updated"]),
                        "path": str(driver.get_db_path()),
                        "is_live": item["is_live"],
                        "model": item["model"],
                        "_ts": (item["time_updated"] or 0) / 1000,
                    })
            except Exception:
                pass

        # Newest first, de-duplicated, then trimmed to the requested limit.
        entries.sort(key=lambda item: item.get("_ts", 0), reverse=True)
        catalog: List[Dict[str, Any]] = []
        seen = set()
        for entry in entries:
            key = (entry["harness"], entry["session_id"])
            if key in seen:
                continue
            seen.add(key)
            entry.pop("_ts", None)
            catalog.append(entry)
            if len(catalog) >= limit:
                break
        return catalog

    def inspect_session(self, harness: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Extract high-density cognitive context for a specific session."""
        if harness == "opencode" or session_id.startswith("ses_"):
            return self._inspect_opencode(session_id)

        try:
            from transmuter import (
                discover_all_sessions,
                AntigravityAdapter,
                CodexAdapter,
                PiAdapter,
                HermesAdapter
            )
        except ImportError:
            return None

        # Find matching session path
        all_raw = discover_all_sessions()
        matched = None
        for s in all_raw:
            if s["harness"] == harness and (s["id"] == session_id or session_id in s["id"]):
                matched = s
                break

        if not matched:
            return None

        p = Path(matched["path"])
        h = matched["harness"]
        sess = None

        if h == "antigravity":
            sess = AntigravityAdapter.parse(p)
        elif h == "codex":
            sess = CodexAdapter.parse(p)
        elif h == "pi":
            sess = PiAdapter.parse(p)

        if not sess:
            return {
                "harness": h,
                "session_id": matched["id"],
                "path": str(p),
                "summary": "Adapter not available for this session type"
            }

        # Extract last turn
        last_turn_text = ""
        for t in reversed(sess.turns):
            if t.get("role") in ("assistant", "user") and t.get("text"):
                last_turn_text = clean_session_title(t["text"])
                break

        return {
            "harness": h,
            "session_id": sess.session_id,
            "title": clean_session_title(sess.title),
            "workspace_root": sess.workspace_root,
            "files_modified": sess.progress_state.get("files_modified", []),
            "files_read": sess.progress_state.get("files_read", []),
            "total_turns": len(sess.turns),
            "last_turn": last_turn_text,
            "created_at": sess.created_at
        }

    def _inspect_opencode(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Inspect an OpenCode session from its native SQLite database."""
        try:
            from hmux.opencode import OpenCodeDriver
        except ImportError:
            return None

        driver = OpenCodeDriver()
        info = driver.get_session_info(session_id)
        if not info:
            return None

        total_turns = 0
        db_path = driver.get_db_path()
        if db_path.exists():
            try:
                conn = driver._connect(db_path)
                row = conn.execute(
                    "SELECT COUNT(*) FROM session_message WHERE session_id = ? AND type = 'user';",
                    (session_id,)
                ).fetchone()
                conn.close()
                total_turns = int(row[0]) if row else 0
            except Exception:
                total_turns = 0

        return {
            "harness": "opencode",
            "session_id": info["session_id"],
            "title": info["title"],
            "model": info["model"] or "OpenCode default",
            "workspace_root": info["directory"],
            "total_turns": total_turns,
            "created_at": self._format_timestamp(info["time_created"]),
            "files_modified": self._parse_opencode_summary_files(
                info.get("summary_diffs")
            ),
            "files_read": [],
            "last_turn": driver.extract_latest_response(session_id) or "",
        }
