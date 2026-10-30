"""
Git Worktree Manager
Provides isolated filesystem sandboxing for each harness without risking the main repository state.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple, List, Dict

class WorktreeError(Exception):
    pass

class WorktreeManager:
    def __init__(self, target_dir: Optional[str] = None):
        self.cwd = os.path.abspath(target_dir or os.getcwd())
        self.repo_root = self._find_repo_root()

    def _find_repo_root(self) -> Optional[str]:
        """Detect the git root directory if inside a git repository."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return None

    @property
    def is_git_repo(self) -> bool:
        return self.repo_root is not None

    def _sanitize_name(self, name: str) -> str:
        """Sanitize branch/task name to safe characters."""
        clean = re.sub(r'[^a-zA-Z0-9_\-]', '-', name.strip())
        clean = re.sub(r'-+', '-', clean).strip('-')
        return clean or "task"

    def ensure_git_exclude(self) -> None:
        """Ensure .hmux_worktrees is ignored locally in .git/info/exclude."""
        if not self.repo_root:
            return
        exclude_file = os.path.join(self.repo_root, ".git", "info", "exclude")
        if os.path.exists(os.path.dirname(exclude_file)):
            existing = ""
            if os.path.exists(exclude_file):
                with open(exclude_file, "r") as f:
                    existing = f.read()
            if ".hmux_worktrees" not in existing:
                with open(exclude_file, "a") as f:
                    f.write("\n# hmux worktree sandbox isolation\n.hmux_worktrees/\n")

    def create_worktree(self, task_name: str, base_ref: str = "HEAD") -> Tuple[str, str]:
        """
        Create a new git worktree on an isolated branch.
        Returns: (worktree_path, branch_name)
        """
        if not self.repo_root:
            raise WorktreeError(f"Current directory '{self.cwd}' is not inside a git repository.")

        self.ensure_git_exclude()
        safe_name = self._sanitize_name(task_name)
        branch_name = f"agent/{safe_name}"
        worktree_base = os.path.join(self.repo_root, ".hmux_worktrees")
        os.makedirs(worktree_base, exist_ok=True)
        worktree_path = os.path.join(worktree_base, safe_name)

        if os.path.exists(worktree_path):
            # Already exists, verify worktree validity
            return worktree_path, branch_name

        # Check if branch already exists
        check_branch = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )

        if check_branch.returncode == 0:
            # Branch exists, check it out into new worktree
            cmd = ["git", "worktree", "add", worktree_path, branch_name]
        else:
            # New branch from base_ref
            cmd = ["git", "worktree", "add", "-b", branch_name, worktree_path, base_ref]

        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        if res.returncode != 0:
            raise WorktreeError(f"Failed to create worktree: {res.stderr.strip()}")

        return worktree_path, branch_name

    def list_worktrees(self) -> List[Dict[str, str]]:
        """List active git worktrees for the current repository."""
        if not self.repo_root:
            return []
        res = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            return []

        worktrees = []
        current: Dict[str, str] = {}
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
            elif line == "bare":
                current["bare"] = "true"
        if current:
            worktrees.append(current)
        return worktrees

    def get_diff(self, branch_name: str, base_ref: str = "HEAD") -> str:
        """Get git diff between base_ref and agent branch."""
        if not self.repo_root:
            raise WorktreeError("Not a git repository.")
        res = subprocess.run(
            ["git", "diff", f"{base_ref}...{branch_name}"],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )
        return res.stdout

    def merge_worktree(self, branch_name: str, target_branch: Optional[str] = None) -> Tuple[bool, str]:
        """Merge the agent's branch back into target_branch (or current branch)."""
        if not self.repo_root:
            raise WorktreeError("Not a git repository.")

        if target_branch:
            subprocess.run(["git", "checkout", target_branch], cwd=self.repo_root, check=True)

        res = subprocess.run(
            ["git", "merge", branch_name, "-m", f"Merge {branch_name} via hmux"],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )
        return (res.returncode == 0, res.stdout or res.stderr)

    def remove_worktree(self, worktree_path: str, delete_branch: bool = False, branch_name: Optional[str] = None) -> None:
        """Remove worktree and optionally prune the branch."""
        if not self.repo_root:
            return
        if os.path.exists(worktree_path):
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                cwd=self.repo_root,
                capture_output=True
            )
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True)

        if delete_branch and branch_name:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=self.repo_root,
                capture_output=True
            )
