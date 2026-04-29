"""Git repository operations for gitcap."""

from __future__ import annotations

from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo

from gitcap.errors import GitCapError


def resolve_repo_path(path: str) -> Path:
    """Expand and resolve a user-provided repository path."""
    repo_path = Path(path).expanduser().resolve()
    if not repo_path.exists():
        raise GitCapError(f"Path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise GitCapError(f"Path is not a directory: {repo_path}")
    if not (repo_path / ".git").exists():
        raise GitCapError(f"Not a git repository: {repo_path}")
    return repo_path


def open_repo(path: str) -> Repo:
    """Open and validate a git repository from a user path."""
    repo_path = resolve_repo_path(path)
    try:
        return Repo(repo_path)
    except InvalidGitRepositoryError as exc:
        raise GitCapError(f"Not a valid git repository: {repo_path}") from exc


def get_staged_diff(repo: Repo) -> str:
    """Return the staged diff for a repository."""
    try:
        diff = repo.git.diff("--staged")
    except GitCommandError as exc:
        raise GitCapError("Could not read staged changes. Make sure git is installed.") from exc

    if not diff.strip():
        raise GitCapError("Nothing staged. Run `git add .` first then try again.")
    return diff


def commit(repo: Repo, message: str) -> str:
    """Create a git commit using the provided message."""
    try:
        output = repo.git.commit("-m", message)
    except GitCommandError as exc:
        stderr = str(exc)
        if "nothing to commit" in stderr.lower():
            raise GitCapError("Nothing staged. Run `git add .` first then try again.") from exc
        raise GitCapError("Git commit failed. Check your repository status and try again.") from exc
    return output
