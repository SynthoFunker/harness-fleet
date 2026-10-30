"""
Freebuff PTY Driver & Headless Conversation Engine
Enables programmatic interaction, prompt injection, and response extraction
for the Freebuff coding assistant within HMUX managed PTY sessions.
"""

import os
import re
import time
import json
import socket
from pathlib import Path
from typing import Optional, List, Tuple

from hmux.state import FleetSession

ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*[\x07\x1b\\]")

def clean_ansi(text: str) -> str:
    """Strip ANSI escape sequences from terminal strings."""
    if not text:
        return ""
    stripped = ANSI_CSI_RE.sub("", text)
    stripped = ANSI_OSC_RE.sub("", stripped)
    return stripped

class FreebuffDriver:
    """
    Manages automated PTY interactions and response extraction for Freebuff.
    """

    @staticmethod
    def get_manicode_projects_dir() -> Path:
        return Path.home() / ".config" / "manicode" / "projects"

    def find_chat_dir(self, worktree_dir: Optional[str] = None) -> Optional[Path]:
        """
        Locate the active chat directory under ~/.config/manicode/projects/<project>/chats/<timestamp>/
        """
        base = self.get_manicode_projects_dir()
        if not base.exists():
            return None

        # 1. Try matching project by worktree directory basename
        if worktree_dir:
            project_name = os.path.basename(os.path.abspath(worktree_dir))
            chats_path = base / project_name / "chats"
            if chats_path.exists():
                dirs = [d for d in chats_path.iterdir() if d.is_dir()]
                if dirs:
                    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
                    return dirs[0]

        # 2. Global fallback: pick the newest chat directory across all projects
        all_chats: List[Tuple[float, Path]] = []
        try:
            for proj in base.iterdir():
                c_dir = proj / "chats"
                if c_dir.exists() and c_dir.is_dir():
                    for d in c_dir.iterdir():
                        if d.is_dir():
                            try:
                                all_chats.append((d.stat().st_mtime, d))
                            except OSError:
                                pass
        except Exception:
            pass

        if all_chats:
            all_chats.sort(key=lambda x: x[0], reverse=True)
            return all_chats[0][1]

        return None

    def get_message_count(self, chat_dir: Optional[Path]) -> int:
        """Count total completed messages in chat-messages.json."""
        if not chat_dir:
            return 0
        msgs_file = chat_dir / "chat-messages.json"
        if msgs_file.exists():
            try:
                data = json.loads(msgs_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return len(data)
            except Exception:
                pass
        meta_file = chat_dir / "chat-meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                return int(meta.get("messageCount", 0))
            except Exception:
                pass
        return 0

    def is_ready(self, session: FleetSession) -> bool:
        """Check if Freebuff PTY is initialized and ready to receive prompts."""
        if not os.path.exists(session.socket_path):
            return False

        # Check log file for prompt readiness indicator
        if os.path.exists(session.log_file):
            try:
                with open(session.log_file, "rb") as f:
                    f.seek(max(0, os.path.getsize(session.log_file) - 10000))
                    raw = f.read().decode("utf-8", errors="ignore")
                    clean = clean_ansi(raw)
                    if "Enter a coding task" in clean or "▍Enter a coding task" in clean:
                        return True
            except Exception:
                pass

        return False

    def wait_until_ready(self, session: FleetSession, timeout: float = 25.0) -> bool:
        """Wait until Freebuff displays the prompt and is ready for input."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_ready(session):
                time.sleep(0.5)
                return True
            time.sleep(0.3)
        return False

    def send_prompt(self, session: FleetSession, prompt: str) -> bool:
        """
        Inject a prompt into Freebuff's running PTY socket followed by carriage return.
        """
        if not os.path.exists(session.socket_path):
            return False

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(session.socket_path)
            clean_prompt = prompt.strip()
            # Send prompt text first
            sock.sendall(clean_prompt.encode("utf-8"))
            time.sleep(0.3)
            # Send Enter (Carriage Return)
            sock.sendall(b"\r")
            time.sleep(0.3)
            sock.close()
            return True
        except Exception:
            return False

    def extract_latest_response(
        self,
        chat_dir: Path,
        min_msg_index: int = 0
    ) -> Optional[str]:
        """
        Extract the latest completed assistant response from chat-messages.json.
        """
        msgs_file = chat_dir / "chat-messages.json"
        if msgs_file.exists():
            try:
                msgs = json.loads(msgs_file.read_text(encoding="utf-8"))
                if isinstance(msgs, list) and len(msgs) > min_msg_index:
                    for m in reversed(msgs[min_msg_index:]):
                        if m.get("variant") in ("ai", "assistant"):
                            if m.get("isComplete") is True:
                                blocks = m.get("blocks", [])
                                text_blocks = [
                                    b.get("content", "").strip()
                                    for b in blocks
                                    if b.get("type") == "text"
                                    and b.get("textType") != "reasoning"
                                    and b.get("content", "").strip()
                                ]
                                if text_blocks:
                                    res = "\n\n".join(text_blocks).strip()
                                    # Clean up interruption tag if present
                                    res = res.replace("\n\n[response interrupted]", "").strip()
                                    return res
                                if m.get("content"):
                                    return m["content"].strip()
            except Exception:
                pass

        # Fallback to log.jsonl inspection
        log_file = chat_dir / "log.jsonl"
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("msg") == "Main prompt finished":
                            continue
                        if "fullResponse" in record.get("data", {}):
                            return record["data"]["fullResponse"].strip()
                        if "fullResponse" in record:
                            return record["fullResponse"].strip()
                    except Exception:
                        pass
            except Exception:
                pass

        return None

    def wait_for_response(
        self,
        session: FleetSession,
        initial_msg_count: int,
        timeout: float = 60.0
    ) -> Optional[str]:
        """
        Wait for Freebuff to process the prompt and return its completed response.
        """
        start = time.time()
        chat_dir = self.find_chat_dir(session.worktree_path)

        while time.time() - start < timeout:
            time.sleep(0.5)

            if not chat_dir:
                chat_dir = self.find_chat_dir(session.worktree_path)
                if not chat_dir:
                    continue

            curr_count = self.get_message_count(chat_dir)
            if curr_count > initial_msg_count:
                response = self.extract_latest_response(chat_dir, min_msg_index=initial_msg_count)
                if response:
                    return response

            # Check log.jsonl as backup
            log_path = chat_dir / "log.jsonl"
            if log_path.exists():
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    for line in reversed(lines[-10:]):
                        if '"msg":"Main prompt finished"' in line or '"fullResponse"' in line:
                            resp = self.extract_latest_response(chat_dir, min_msg_index=initial_msg_count)
                            if resp:
                                return resp
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
        Execute a full conversation turn with Freebuff:
        1. Wait for readiness
        2. Record initial state
        3. Send prompt
        4. Wait for and return completed response
        """
        if not self.wait_until_ready(session, timeout=25.0):
            return None

        chat_dir = self.find_chat_dir(session.worktree_path)
        initial_count = self.get_message_count(chat_dir)

        if not self.send_prompt(session, prompt):
            return None

        return self.wait_for_response(session, initial_msg_count=initial_count, timeout=timeout)
