"""
HMUX Universal Session Catalog
Scans and parses native sessions across all harnesses (Antigravity, Codex, Pi, Hermes, Freebuff)
to extract real conversation titles, goals, touched files, and metadata.
"""

import os
import sys
import json
import ast
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

    def list_all_sessions(
        self,
        harness_filter: Optional[str] = None,
        limit: int = 35
    ) -> List[Dict[str, Any]]:
        """
        Discovers historical and active sessions across all supported harnesses,
        returning their session IDs, real titles, and status.
        """
        try:
            from transmuter import (
                discover_all_sessions,
                AntigravityAdapter,
                CodexAdapter,
                PiAdapter,
                HermesAdapter
            )
        except ImportError:
            return []

        all_raw = discover_all_sessions()
        if harness_filter:
            all_raw = [s for s in all_raw if s["harness"] == harness_filter]

        # Sort by file mtime descending
        def get_mtime(item):
            try:
                p = Path(item["path"])
                return p.stat().st_mtime if p.exists() else 0
            except Exception:
                return 0

        all_raw.sort(key=get_mtime, reverse=True)
        catalog = []

        # Find live sessions to cross-reference
        live_sessions = {s.session_id: s for s in self.state_mgr.list_all(refresh=True) if s.status == "RUNNING"}

        for item in all_raw[:limit]:
            h = item["harness"]
            sid = item["id"]
            p = Path(item["path"])
            mtime = get_mtime(item)
            dt_str = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%d %H:%M") if mtime else "-"

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

            is_live = sid in live_sessions or any(sid in s_id for s_id in live_sessions)

            catalog.append({
                "harness": h,
                "session_id": sid,
                "title": title or "Interactive Session",
                "updated_at": dt_str,
                "path": str(p),
                "is_live": is_live
            })

        return catalog

    def inspect_session(self, harness: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Extract high-density cognitive context for a specific session."""
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
