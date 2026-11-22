"""Shared fixtures. Every test runs with a throwaway HOME, config dir and PATH,
so real agents, real credentials and the user's settings are never touched."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes import FakeServer

GIT = shutil.which("git")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """Directory on PATH where tests drop fake agent CLIs."""
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def env(home: Path, bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Isolated environment, applied to this process and returned for subprocesses."""
    assert GIT, "git must be installed to run the tests"
    values = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        # Fake agents first, then only git's directory, so real agent CLIs are invisible.
        "PATH": os.pathsep.join([str(bin_dir), str(Path(GIT).parent)]),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GITCAP_LOCAL_URL": "http://127.0.0.1:9",  # discard port: no local server, fails instantly
        "COLUMNS": "100",
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    for key in ("GROQ_API_KEY", "GROQ_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
                "GEMINI_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
                "CURSOR_API_KEY", "GITCAP_NO_UPDATE_CHECK", "GITCAP_PYPI_URL", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "OLLAMA_HOST", "VISUAL", "EDITOR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return {**values, "PYTHONPATH": os.pathsep.join(sys.path)}


def git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    """An empty git repository with an identity configured."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")
    return path


@pytest.fixture
def staged_repo(repo: Path) -> Path:
    """A repo with one prior commit and a staged change."""
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "chore: init")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    git(repo, "add", ".")
    return repo


@pytest.fixture
def server():
    fake = FakeServer().start()
    yield fake
    fake.stop()


@pytest.fixture
def run_cli(env: dict[str, str]):
    """Run `python -m gitcap ...` in a subprocess with the isolated environment."""

    def run(*args: str, extra_env: dict[str, str] | None = None, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "gitcap", *args],
            input=stdin,
            capture_output=True,
            text=True,
            env={**env, **(extra_env or {})},
            timeout=60,
        )

    return run
