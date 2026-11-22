"""Config storage for gitcap: a small JSON file, readable only by the user."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from gitcap.errors import GitCapError

AUTO = "auto"


@dataclass(slots=True)
class GitCapConfig:
    """Persisted settings. Every field is optional; defaults mean "auto-detect"."""

    backend: str = AUTO  # "auto", an agent name, "local", or "groq"
    model: str | None = None  # model override for the chosen backend
    groq_api_key: str | None = None
    local_url: str | None = None  # a local model server other than Ollama/LM Studio defaults

    @property
    def groq_key(self) -> str | None:
        return os.environ.get("GROQ_API_KEY") or self.groq_api_key


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "gitcap"


def config_file() -> Path:
    return config_dir() / "config.json"


def load_config() -> GitCapConfig:
    path = config_file()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return GitCapConfig()
    except (OSError, ValueError) as exc:
        raise GitCapError(f"Config at {path} could not be read. Run `gitcap config --reset`.") from exc
    known = {f.name for f in fields(GitCapConfig)}
    return GitCapConfig(**{key: value for key, value in data.items() if key in known})


def save_config(config: GitCapConfig) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = config_file()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({k: v for k, v in asdict(config).items() if v is not None}, handle, indent=2)
    os.chmod(path, 0o600)


def reset_config() -> None:
    try:
        config_file().unlink(missing_ok=True)
    except OSError as exc:
        raise GitCapError("Could not clear saved config. Check file permissions.") from exc


def summary(config: GitCapConfig) -> str:
    groq = "set" if config.groq_key else "not set"
    line = f"backend={config.backend}  model={config.model or 'default'}  groq_key={groq}"
    return line + (f"  local_url={config.local_url}" if config.local_url else "")
