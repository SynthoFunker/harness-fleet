"""
Harness Registry & Command Builder
Detects installed AI coding harnesses and prepares commands for execution.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Optional, List, Tuple

_HOME = str(Path.home())

HARNESS_DEFAULTS = {
    "antigravity": {
        "aliases": ["agy", "antigravity", "gemini"],
        "paths": [os.path.join(_HOME, ".local/bin/agy"), "agy"],
        "description": "Google Antigravity CLI (Multi-agent architecture & artifacts)",
    },
    "codex": {
        "aliases": ["codex"],
        "paths": [os.path.join(_HOME, ".local/bin/codex"), "codex"],
        "description": "Codex CLI (Targeted execution & sandbox tasks)",
    },
    "opencode": {
        "aliases": ["opencode", "open-code"],
        "paths": [os.path.join(_HOME, ".opencode/bin/opencode"), "opencode"],
        "description": "OpenCode CLI (Open-source AI coding agent)",
    },
    "hermes": {
        "aliases": ["hermes"],
        "paths": [os.path.join(_HOME, ".local/bin/hermes"), "hermes"],
        "description": "Hermes Agent (Tool-calling terminal assistant)",
    },
    "pi": {
        "aliases": ["pi", "pi-coding-agent"],
        "paths": [os.path.join(_HOME, ".pi/agent/bin/pi"), "pi"],
        "description": "Pi AI coding assistant (Lightweight & extensions)",
    },
    "freebuff": {
        "aliases": ["freebuff", "manicode"],
        "paths": ["/usr/local/bin/freebuff", "freebuff"],
        "description": "Freebuff coding assistant",
    },
    "claude": {
        "aliases": ["claude", "claude-code"],
        "paths": ["claude"],
        "description": "Claude Code CLI",
    },
    "aider": {
        "aliases": ["aider"],
        "paths": ["aider"],
        "description": "Aider CLI",
    },
}

# System daemons and audio/multimedia services that should never be identified as AI harnesses
SYSTEM_EXCLUSIONS = {
    "pipewire",
    "pipewire-pulse",
    "pipewire-media-session",
    "wireplumber",
    "pulseaudio",
    "systemd",
    "dbus-daemon",
    "dbus-broker",
}

INTERPRETER_NAMES = {
    "node",
    "nodejs",
    "python",
    "python3",
    "python3.14",
    "bash",
    "sh",
    "zsh",
    "bun",
    "deno",
}


class HarnessInfo:
    def __init__(self, key: str, name: str, binary_path: str, description: str):
        self.key = key
        self.name = name
        self.binary_path = binary_path
        self.description = description

    def __repr__(self):
        return f"<HarnessInfo {self.key}: {self.binary_path}>"


class HarnessRegistry:
    def __init__(self):
        self._installed: Dict[str, HarnessInfo] = {}
        self.scan()

    def scan(self) -> Dict[str, HarnessInfo]:
        """Scan PATH and standard locations for installed harnesses."""
        self._installed = {}
        for canonical_name, config in HARNESS_DEFAULTS.items():
            for p in config["paths"]:
                resolved = shutil.which(p) or (p if os.path.isabs(p) and os.path.exists(p) and os.access(p, os.X_OK) else None)
                if resolved:
                    self._installed[canonical_name] = HarnessInfo(
                        key=canonical_name,
                        name=canonical_name,
                        binary_path=os.path.abspath(resolved),
                        description=config["description"]
                    )
                    break
        return self._installed

    def get(self, name: str) -> Optional[HarnessInfo]:
        """Get harness by canonical name or alias."""
        clean = name.strip().lower()
        if clean in self._installed:
            return self._installed[clean]
        for canonical, config in HARNESS_DEFAULTS.items():
            if clean in config["aliases"]:
                return self._installed.get(canonical)
        return None

    def list_installed(self) -> List[HarnessInfo]:
        """Return list of detected harnesses."""
        return list(self._installed.values())

    def resolve_model(self, harness_key: str, explicit_model: Optional[str] = None) -> str:
        """
        Determine the real active AI model name for a harness, resolving explicit flags
        or falling back to the harness's local user configuration files.
        """
        if explicit_model:
            return explicit_model

        clean = (harness_key or "").strip().lower()
        home = Path.home()

        if clean in ("codex",):
            config_file = home / ".codex" / "config.toml"
            if config_file.exists():
                try:
                    import re
                    m = re.search(r'^\s*model\s*=\s*["\']([^"\']+)["\']', config_file.read_text(), re.M)
                    if m:
                        return m.group(1)
                except Exception:
                    pass
            return "gpt-4o"

        if clean in ("pi", "pi-coding-agent"):
            settings_file = home / ".pi" / "agent" / "settings.json"
            if settings_file.exists():
                try:
                    import json
                    data = json.loads(settings_file.read_text())
                    if data.get("defaultModel"):
                        return data["defaultModel"]
                except Exception:
                    pass
            return "flint-free"

        if clean in ("antigravity", "agy", "gemini"):
            settings_file = home / ".gemini" / "antigravity-cli" / "settings.json"
            if settings_file.exists():
                try:
                    import json
                    data = json.loads(settings_file.read_text())
                    if data.get("model"):
                        return data["model"]
                except Exception:
                    pass
            return "Gemini 3.8 Flash"

        if clean in ("opencode", "open-code"):
            return "claude-3-5-sonnet"

        if clean in ("hermes",):
            return "hermes-3-70b"

        if clean in ("claude", "claude-code"):
            return "claude-3-5-sonnet"

        if clean in ("aider",):
            return "gpt-4o"

        return "standard"

    def match_process(self, proc_name: str, cmdline: List[str]) -> Optional[str]:
        """
        Check if a running process corresponds to a registered harness.
        Returns the harness canonical key or None.
        """
        proc_lower = (proc_name or "").strip().lower()

        # Immediate filter for system daemons and audio services (e.g. pipewire)
        if proc_lower in SYSTEM_EXCLUSIONS or proc_lower.startswith("pipewire"):
            return None

        # Immediate filter for hmux itself
        if proc_lower in ("hmux", "hmux_cli"):
            return None

        if not cmdline:
            if not proc_lower:
                return None
            for canonical, config in HARNESS_DEFAULTS.items():
                if any(alias.lower() == proc_lower for alias in config["aliases"]):
                    return canonical
            return None

        raw_exe = cmdline[0]
        exe_base = os.path.basename(raw_exe).lower()

        # Filter out system binaries
        if exe_base in SYSTEM_EXCLUSIONS or exe_base.startswith("pipewire"):
            return None

        # Avoid matching hmux process invocations
        if exe_base in ("hmux", "hmux_cli.py"):
            return None
        if any(exe_base == interp or exe_base.startswith(interp) for interp in ("python", "node", "bash", "sh")):
            for arg in cmdline[1:3]:
                base_arg = os.path.basename(arg).lower()
                if base_arg in ("hmux_cli.py", "worker.py", "mcp_server.py") or base_arg == "hmux":
                    return None

        # Extract target script if executed via interpreter
        script_path = None
        script_base = None
        is_interpreter = any(exe_base == interp or exe_base.startswith(interp) for interp in INTERPRETER_NAMES)
        if is_interpreter and len(cmdline) > 1:
            for i, arg in enumerate(cmdline[1:], start=1):
                if arg == "-m" and i + 1 < len(cmdline):
                    script_path = cmdline[i + 1]
                    script_base = script_path.lower()
                    break
                elif not arg.startswith("-"):
                    script_path = arg
                    script_base = os.path.basename(arg).lower()
                    break

        candidate_paths = [raw_exe]
        if script_path:
            candidate_paths.append(script_path)

        candidate_names = {exe_base}
        if proc_lower:
            candidate_names.add(proc_lower)
        if script_base:
            candidate_names.add(script_base)

        # 1. Exact binary path matching against installed harnesses
        for key, info in self._installed.items():
            if not info.binary_path:
                continue
            installed_bin = os.path.realpath(info.binary_path)
            for c_path in candidate_paths:
                if c_path == info.binary_path or c_path == installed_bin:
                    return key
                try:
                    if os.path.isabs(c_path) and os.path.exists(c_path):
                        if os.path.realpath(c_path) == installed_bin:
                            return key
                except Exception:
                    pass

        # 2. Exact name and alias matching
        for canonical, config in HARNESS_DEFAULTS.items():
            for alias in config["aliases"]:
                alias_clean = alias.lower()
                if alias_clean in candidate_names:
                    return canonical

        # 3. Path-specific signature matching for multi-token / packaged harnesses
        if script_path:
            norm_script = script_path.lower()
            if "pi-coding-agent" in norm_script or ".pi/agent" in norm_script:
                return "pi"
            if "freebuff" in norm_script:
                return "freebuff"

        return None

    def build_command(
        self,
        harness_name: str,
        worktree_dir: str,
        prompt: Optional[str] = None,
        headless: bool = False,
        extra_args: Optional[List[str]] = None,
        model: Optional[str] = None
    ) -> Tuple[List[str], Dict[str, str]]:
        """
        Build process command and environment variables for a given harness.
        """
        harness = self.get(harness_name)
        if not harness:
            # Fallback to direct binary if user passed custom path
            resolved = shutil.which(harness_name)
            if resolved:
                harness = HarnessInfo(harness_name, harness_name, resolved, "Custom binary")
            else:
                raise ValueError(f"Unknown or uninstalled harness: '{harness_name}'")

        extra = extra_args or []
        env = os.environ.copy()
        cmd: List[str] = [harness.binary_path]

        key = harness.key
        if key == "codex":
            cmd = [harness.binary_path]
            if headless and prompt:
                cmd.append("exec")
            if model:
                cmd.extend(["-m", model])
            cmd.extend(["-C", worktree_dir])
            if not os.path.exists(os.path.join(worktree_dir, ".git")):
                cmd.append("--skip-git-repo-check")
            if prompt:
                cmd.append(prompt)
            cmd.extend(extra)

        elif key == "opencode":
            if headless and prompt:
                cmd = [harness.binary_path, "run"]
                if model:
                    cmd.extend(["-m", model])
                cmd.extend(extra)
                cmd.append(prompt)
            else:
                cmd = [harness.binary_path]
                if model:
                    cmd.extend(["-m", model])
                cmd.extend(extra)
                if prompt:
                    cmd.append(prompt)

        elif key == "hermes":
            cmd = [harness.binary_path, "--in", worktree_dir]
            if model:
                cmd.extend(["--model", model])
            if prompt:
                cmd.extend(["-z", prompt])
            cmd.extend(extra)

        elif key == "pi":
            cmd = [harness.binary_path]
            if model:
                cmd.extend(["--model", model])
            if headless and prompt:
                cmd.append("-p")
            cmd.extend(extra)
            if prompt:
                cmd.append(prompt)

        elif key == "freebuff":
            cmd = [harness.binary_path, "--cwd", worktree_dir]
            if model:
                cmd.extend(["--model", model])
            cmd.extend(extra)
            if prompt:
                cmd.append(prompt)

        elif key == "antigravity":
            cmd = [harness.binary_path, "--add-dir", worktree_dir]
            if model:
                cmd.extend(["--model", model])
            if headless and prompt:
                cmd.extend(["--input-format", "text"])
            if prompt:
                cmd.extend(["-i", prompt])
            cmd.extend(extra)

        elif key == "claude":
            cmd = [harness.binary_path]
            if model:
                cmd.extend(["--model", model])
            if headless and prompt:
                cmd.extend(["-p", prompt])
            elif prompt:
                cmd.append(prompt)
            cmd.extend(extra)

        elif key == "aider":
            cmd = [harness.binary_path]
            if model:
                cmd.extend(["--model", model])
            cmd.extend(extra)
            if prompt:
                cmd.extend(["--message", prompt])

        else:
            if model:
                cmd.extend(["--model", model])
            if prompt:
                cmd.append(prompt)
            cmd.extend(extra)

        return cmd, env
