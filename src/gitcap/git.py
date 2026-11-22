"""Git operations via the git binary (no GitPython import cost)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from gitcap.errors import GitCapError

NOISY_FILES = ("lock", ".min.js", ".min.css", ".map", ".svg", ".snap")
NOISY_NAMES = {"uv.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "Cargo.lock", "go.sum", "bun.lockb", "Gemfile.lock", "composer.lock"}


def _git(root: Path | str, *args: str, stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise GitCapError("git is not installed or not on PATH.") from exc
    if proc.returncode != 0:
        raise GitCapError(proc.stderr.strip() or f"git {args[0]} failed.")
    return proc.stdout


def repo_root(path: str = ".") -> Path:
    """Resolve the top-level of the repo containing path (works from subdirectories and worktrees)."""
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise GitCapError(f"Not a directory: {target}")
    try:
        return Path(_git(target, "rev-parse", "--show-toplevel").strip())
    except GitCapError:
        raise GitCapError(f"Not inside a git repository: {target}") from None


def _is_noisy(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in NOISY_NAMES or name.endswith(NOISY_FILES)


def _split_files(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into (path, chunk) per file."""
    chunks: list[tuple[str, str]] = []
    for part in diff.split("\ndiff --git "):
        if not part.strip():
            continue
        chunk = part if part.startswith("diff --git ") else "diff --git " + part
        header = chunk.split("\n", 1)[0]
        path = header.rsplit(" b/", 1)[-1] if " b/" in header else header
        chunks.append((path, chunk))
    return chunks


@dataclass(slots=True)
class StagedChanges:
    """The staged diff, read once and trimmed to fit each backend's budget on demand."""

    stat: str
    files: list[tuple[str, str]]
    skipped: list[str]

    @property
    def full_size(self) -> int:
        return len(self.stat) + sum(len(chunk) for _, chunk in self.files)

    def render(self, budget: int) -> str:
        """--stat summary first, then per-file diffs shared fairly within budget characters."""
        remaining = budget - len(self.stat)
        parts: list[str] = []
        # Smallest files first so one huge file can't starve the rest.
        ordered = sorted(self.files, key=lambda item: len(item[1]))
        for index, (path, chunk) in enumerate(ordered):
            share = max(remaining // (len(ordered) - index), 0)
            if len(chunk) > share:
                header = chunk.split("\n", 1)[0]
                chunk = (chunk[:share] if share > len(header) else header) + f"\n... ({path} truncated)"
            parts.append(chunk)
            remaining -= len(chunk)
        if self.skipped:
            parts.append("Generated/lock files changed (content omitted): " + ", ".join(self.skipped))
        return f"Summary:\n{self.stat}\n\n" + "\n".join(parts)


def staged_changes(root: Path) -> StagedChanges:
    """Read the staged diff once."""
    stat = _git(root, "diff", "--staged", "--stat=100").rstrip().lstrip("\n")
    if not stat:
        raise GitCapError("Nothing staged. Run `git add` first, then try again.")
    # Force a/ b/ prefixes: user settings like diff.mnemonicPrefix or diff.noprefix would
    # otherwise change the headers we read file paths from.
    raw = _git(root, "diff", "--staged", "--no-color", "--no-ext-diff", "-U2", "--src-prefix=a/", "--dst-prefix=b/")
    files = _split_files(raw)
    return StagedChanges(
        stat=stat,
        files=[(p, c) for p, c in files if not _is_noisy(p)],
        skipped=[p for p, _ in files if _is_noisy(p)],
    )


def recent_subjects(root: Path, count: int = 8) -> list[str]:
    """Recent commit subjects, used as style examples. Empty for a new repo."""
    try:
        return [line for line in _git(root, "log", f"-{count}", "--format=%s").splitlines() if line.strip()]
    except GitCapError:
        return []


def commit(root: Path, message: str) -> str:
    """Create a commit with message (read from stdin, so any length or characters are safe)."""
    try:
        return _git(root, "commit", "-F", "-", stdin=message).strip()
    except GitCapError as exc:
        if "nothing to commit" in str(exc).lower():
            raise GitCapError("Nothing staged. Run `git add` first, then try again.") from None
        raise GitCapError(f"git commit failed: {exc}") from None
