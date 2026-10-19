"""Find files and changed line ranges with git."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


class GitError(RuntimeError):
    """Raised when a git command fails."""


def _git(args: list[str], cwd: str | Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def parse_changed_lines(diff_text: str) -> set[int]:
    """Return new-side line numbers touched by a unified diff produced with `-U0`."""
    lines: set[int] = set()
    for match in _HUNK.finditer(diff_text):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        lines.update(range(start, start + count))
    return lines


def changed_files(base_ref: str, cwd: str | Path = ".") -> list[str]:
    """Files added, copied, modified or renamed between `base_ref` and HEAD."""
    output = _git(["diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...HEAD"], cwd)
    return [line for line in output.splitlines() if line]


def changed_lines(base_ref: str, path: str, cwd: str | Path = ".") -> set[int]:
    """Line numbers in `path` (at HEAD) that differ from `base_ref`."""
    return parse_changed_lines(_git(["diff", "-U0", f"{base_ref}...HEAD", "--", path], cwd))


def all_files(cwd: str | Path = ".") -> list[str]:
    """Tracked files, or every file on disk when not in a git repository."""
    try:
        return [line for line in _git(["ls-files"], cwd).splitlines() if line]
    except (GitError, FileNotFoundError):
        root = Path(cwd)
        skip = {".git", ".venv", ".venv-conda", "node_modules", "__pycache__"}
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                found.append(Path(dirpath, name).relative_to(root).as_posix())
        return sorted(found)
