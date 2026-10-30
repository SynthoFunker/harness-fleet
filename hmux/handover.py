"""
Cross-Harness Handover Engine
Integrates with session-transmuter to achieve zero-token context migration between harnesses.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

TRANSMUTER_PATHS = [
    str(Path.home() / ".local" / "bin" / "session-transmuter"),
    "session-transmuter"
]

class HandoverError(Exception):
    pass

class HandoverEngine:
    def __init__(self):
        self.transmuter_bin = self._find_transmuter()

    def _find_transmuter(self) -> Optional[str]:
        for p in TRANSMUTER_PATHS:
            resolved = shutil.which(p) or (p if os.path.exists(p) and os.access(p, os.X_OK) else None)
            if resolved:
                return os.path.abspath(resolved)
        return None

    @property
    def is_available(self) -> bool:
        return self.transmuter_bin is not None

    def get_summary(self, source_harness: str, session_id: Optional[str] = None) -> str:
        """
        Fetch high-density (<150 token) cognitive summary via session-transmuter.
        """
        if not self.transmuter_bin:
            raise HandoverError("session-transmuter not found in PATH or ~/.local/bin.")

        cmd = [self.transmuter_bin, "summary", source_harness]
        if session_id:
            cmd.append(session_id)

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise HandoverError(f"Failed to fetch summary: {res.stderr.strip() or res.stdout.strip()}")

        return res.stdout.strip()

    def generate_handover_prompt(
        self,
        source_harness: str,
        session_id: Optional[str],
        user_prompt: str
    ) -> str:
        """
        Build a concise prompt that seeds the target harness with zero-token handover state.
        """
        summary = self.get_summary(source_harness, session_id)
        directive = user_prompt.strip() if user_prompt else "Continue work from the prior session context above."
        formatted = (
            f"=== PRIOR CONTEXT HANDOVER ({source_harness}) ===\n"
            f"{summary}\n"
            f"================================================\n\n"
            f"CURRENT DIRECTIVE:\n{directive}"
        )
        return formatted

    def migrate_session(
        self,
        from_harness: str,
        to_harness: str,
        session_id: Optional[str] = None
    ) -> str:
        """
        Run programmatic out-of-band migration between harnesses.
        """
        if not self.transmuter_bin:
            raise HandoverError("session-transmuter not found.")

        cmd = [
            self.transmuter_bin, "migrate",
            "--from-harness", from_harness,
            "--to-harness", to_harness
        ]
        if session_id:
            cmd.append(session_id)

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise HandoverError(f"Migration failed: {res.stderr.strip()}")
        return res.stdout.strip()
