"""Detect and drive the AI coding agents the user is already logged in to.

Like a token-usage scanner, detection only looks at files the agents already keep
on disk (binaries on PATH, auth files, session folders). Nothing is executed
until a message is actually generated.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gitcap.errors import GitCapError
from gitcap.result import Generation, Usage

HOME = Path.home()
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
AGENT_TIMEOUT_SECONDS = 180
# Agents have 200k+ token windows, so the budget is about cost and speed, not fit.
STDIN_BUDGET_CHARS = 100_000
# Prompts passed as a command-line argument must stay under Linux's 128 KiB per-argument limit.
ARGV_BUDGET_CHARS = 60_000


@dataclass(frozen=True, slots=True)
class Agent:
    """A coding-agent CLI that can answer a single prompt non-interactively."""

    name: str
    label: str
    binaries: tuple[str, ...]
    activity_dirs: tuple[Path, ...]
    auth_files: tuple[Path, ...] = ()
    auth_env: tuple[str, ...] = ()
    default_model: str | None = None
    budget_chars: int = ARGV_BUDGET_CHARS
    # Returns (argv after the binary, stdin text or None).
    build: Callable[[str, str | None], tuple[list[str], str | None]] = field(repr=False, default=None)  # type: ignore[assignment]
    parse: Callable[[str], Generation] = field(repr=False, default=None)  # type: ignore[assignment]

    def logged_in(self) -> bool | None:
        """True/False when auth can be checked from disk, None when unknown."""
        if any(os.environ.get(var) for var in self.auth_env):
            return True
        if not self.auth_files:
            return None
        return any(path.exists() for path in self.auth_files)


@dataclass(slots=True)
class Detection:
    """What gitcap found on disk for one agent."""

    agent: Agent
    binary: str | None
    logged_in: bool | None
    last_used: float | None

    @property
    def usable(self) -> bool:
        return self.binary is not None and self.logged_in is not False


# --------------------------------------------------------------------------- parsers


def _json_objects(stdout: str) -> list[dict]:
    """Parse stdout as one JSON document, or as JSON lines, ignoring noise."""
    text = stdout.strip()
    try:
        value = json.loads(text)
        return [value] if isinstance(value, dict) else []
    except ValueError:
        pass
    objects = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                objects.append(value)
    return objects


def _usage_from_claude_style(data: dict) -> tuple[Usage, str | None]:
    """Read usage from Claude Code / Qoder / Grok style result JSON."""
    model_usage = data.get("modelUsage") or {}
    models = [name for name in model_usage if not name.startswith("<")]
    if model_usage:
        usage = Usage()
        for stats in model_usage.values():
            usage.input_tokens += int(stats.get("inputTokens") or 0)
            usage.output_tokens += int(stats.get("outputTokens") or 0)
            usage.cache_read_tokens += int(stats.get("cacheReadInputTokens") or 0)
            usage.cache_write_tokens += int(stats.get("cacheCreationInputTokens") or 0)
        usage.cost_usd = data.get("total_cost_usd")
        return usage, ", ".join(models) or None
    raw = data.get("usage") or {}
    return (
        Usage(
            input_tokens=int(raw.get("input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
            cache_read_tokens=int(raw.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(raw.get("cache_creation_input_tokens") or 0),
            cost_usd=data.get("total_cost_usd"),
        ),
        None,
    )


def parse_result_json(stdout: str) -> Generation:
    """Parse `{"result"|"text"|"response": ..., "usage": ...}` style output."""
    objects = _json_objects(stdout)
    data = next((obj for obj in reversed(objects) if obj.get("type") == "result"), objects[-1] if objects else None)
    if data is None:
        return Generation(text=stdout.strip())
    if data.get("is_error"):
        errors = data.get("errors") or [data.get("result") or data.get("subtype") or "unknown error"]
        raise GitCapError("; ".join(str(err) for err in errors))
    text = data.get("result") or data.get("text") or data.get("response") or ""
    usage, model = _usage_from_claude_style(data)
    return Generation(text=str(text), usage=usage, model=model)


def parse_codex_jsonl(stdout: str) -> Generation:
    """Parse `codex exec --json` event lines."""
    text, usage = "", None
    for event in _json_objects(stdout):
        kind = event.get("type")
        item = event.get("item") or {}
        if kind == "item.completed" and item.get("type") in {"agent_message", "assistant_message"}:
            text = item.get("text") or text
        elif kind == "turn.completed":
            raw = event.get("usage") or {}
            cached = int(raw.get("cached_input_tokens") or 0)
            usage = Usage(
                input_tokens=int(raw.get("input_tokens") or 0) - cached,
                output_tokens=int(raw.get("output_tokens") or 0),
                cache_read_tokens=cached,
            )
        elif kind in {"error", "turn.failed"}:
            message = event.get("message") or (event.get("error") or {}).get("message") or "codex failed"
            raise GitCapError(str(message))
    return Generation(text=text, usage=usage)


def parse_gemini_json(stdout: str) -> Generation:
    """Parse `gemini --output-format json` output."""
    objects = _json_objects(stdout)
    if not objects:
        return Generation(text=stdout.strip())
    data = objects[-1]
    if data.get("error"):
        raise GitCapError(str((data["error"] or {}).get("message") or data["error"]))
    usage, models = Usage(), []
    for name, stats in ((data.get("stats") or {}).get("models") or {}).items():
        tokens = stats.get("tokens") or {}
        cached = int(tokens.get("cached") or 0)
        usage.input_tokens += int(tokens.get("prompt") or 0) - cached
        usage.cache_read_tokens += cached
        usage.output_tokens += int(tokens.get("candidates") or 0) + int(tokens.get("thoughts") or 0)
        models.append(name)
    return Generation(text=str(data.get("response") or ""), usage=usage if models else None, model=", ".join(models) or None)


def parse_plain(stdout: str) -> Generation:
    """Plain text output: the whole of stdout is the message."""
    return Generation(text=stdout.strip())


# --------------------------------------------------------------------------- argv builders


def _with_model(args: list[str], model: str | None, flag: str = "--model") -> list[str]:
    return [*args, flag, model] if model else args


def _build_claude(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    args = [
        "-p",
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources", "",
        # Replace the large default agent system prompt: fewer tokens, faster reply.
        "--system-prompt", "You write git commit messages. Reply with the commit message only.",
    ]
    return _with_model(args, model), prompt


def _build_qoder(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    args = ["-p", "-o", "json", "--tools", "",
            "--system-prompt", "You write git commit messages. Reply with the commit message only."]
    return _with_model(args, model), prompt


def _build_codex(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    args = ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never"]
    return [*_with_model(args, model), "-"], prompt


def _build_gemini(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    return _with_model(["--output-format", "json", "-p", prompt], model), None


def _build_grok(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    args = ["-p", prompt, "--output-format", "json", "--tools", "", "--no-subagents", "--disable-web-search"]
    return _with_model(args, model), None


def _build_cursor(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    return _with_model(["-p", "--output-format", "json", prompt], model), None


def _build_copilot(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    return _with_model(["-p", prompt, "--silent"], model), None


def _build_opencode(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    return _with_model(["run", prompt], model), None


def _build_kimi(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    # --quiet = --print --output-format text --final-message-only. Print mode auto-approves
    # tools, which is why every agent runs in an empty temporary folder.
    return _with_model(["--quiet", "-p", prompt], model, "-m"), None


def _build_crush(prompt: str, model: str | None) -> tuple[list[str], str | None]:
    return _with_model(["run", "--quiet"], model), prompt


# --------------------------------------------------------------------------- registry

AGENTS: tuple[Agent, ...] = (
    Agent(
        name="claude",
        label="Claude Code",
        binaries=("claude",),
        activity_dirs=(HOME / ".claude" / "projects", XDG_CONFIG / "claude" / "projects"),
        auth_files=(HOME / ".claude" / ".credentials.json", HOME / ".claude.json"),
        auth_env=("ANTHROPIC_API_KEY",),
        default_model="haiku",
        budget_chars=STDIN_BUDGET_CHARS,
        build=_build_claude,
        parse=parse_result_json,
    ),
    Agent(
        name="codex",
        label="Codex",
        binaries=("codex",),
        activity_dirs=(Path(os.environ.get("CODEX_HOME") or HOME / ".codex") / "sessions",),
        auth_files=(Path(os.environ.get("CODEX_HOME") or HOME / ".codex") / "auth.json",),
        auth_env=("OPENAI_API_KEY", "CODEX_API_KEY"),
        budget_chars=STDIN_BUDGET_CHARS,
        build=_build_codex,
        parse=parse_codex_jsonl,
    ),
    Agent(
        name="gemini",
        label="Gemini CLI",
        binaries=("gemini",),
        activity_dirs=(HOME / ".gemini" / "tmp",),
        auth_files=(HOME / ".gemini" / "oauth_creds.json",),
        auth_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        build=_build_gemini,
        parse=parse_gemini_json,
    ),
    Agent(
        name="grok",
        label="Grok CLI",
        binaries=("grok",),
        activity_dirs=(HOME / ".grok" / "sessions",),
        auth_files=(HOME / ".grok" / "auth.json",),
        auth_env=("XAI_API_KEY",),
        build=_build_grok,
        parse=parse_result_json,
    ),
    Agent(
        name="qoder",
        label="Qoder CLI",
        binaries=("qodercli",),
        activity_dirs=(HOME / ".qoder" / "projects",),
        budget_chars=STDIN_BUDGET_CHARS,
        build=_build_qoder,
        parse=parse_result_json,
    ),
    Agent(
        name="kimi",
        label="Kimi Code",
        binaries=("kimi",),
        activity_dirs=(
            Path(os.environ.get("KIMI_CODE_HOME") or HOME / ".kimi-code") / "sessions",
            Path(os.environ.get("KIMI_SHARE_DIR") or HOME / ".kimi") / "sessions",
        ),
        auth_env=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        build=_build_kimi,
        parse=parse_plain,
    ),
    Agent(
        name="opencode",
        label="OpenCode",
        binaries=("opencode",),
        activity_dirs=(Path(os.environ.get("OPENCODE_DATA_DIR") or XDG_DATA / "opencode"),),
        auth_files=(Path(os.environ.get("OPENCODE_DATA_DIR") or XDG_DATA / "opencode") / "auth.json",),
        build=_build_opencode,
        parse=parse_plain,
    ),
    Agent(
        name="cursor",
        label="Cursor Agent",
        binaries=("cursor-agent",),
        activity_dirs=(HOME / ".cursor" / "chats", XDG_CONFIG / "cursor"),
        auth_env=("CURSOR_API_KEY",),
        build=_build_cursor,
        parse=parse_result_json,
    ),
    Agent(
        name="copilot",
        label="Copilot CLI",
        binaries=("copilot",),
        activity_dirs=(Path(os.environ.get("COPILOT_HOME") or HOME / ".copilot") / "session-state",),
        build=_build_copilot,
        parse=parse_plain,
    ),
    Agent(
        name="crush",
        label="Crush",
        binaries=("crush",),
        activity_dirs=(XDG_DATA / "crush", XDG_CONFIG / "crush"),
        budget_chars=STDIN_BUDGET_CHARS,
        build=_build_crush,
        parse=parse_plain,
    ),
)

AGENTS_BY_NAME: dict[str, Agent] = {agent.name: agent for agent in AGENTS}
ALIASES: dict[str, str] = {"kimi-code": "kimi", "kimi-cli": "kimi", "claude-code": "claude", "qodercli": "qoder", "cursor-agent": "cursor", "copilot-cli": "copilot"}


def get_agent(name: str) -> Agent:
    key = ALIASES.get(name.strip().lower(), name.strip().lower())
    if key not in AGENTS_BY_NAME:
        raise GitCapError(f"Unknown agent `{name}`. Choose one of: {', '.join(AGENTS_BY_NAME)}, local, groq.")
    return AGENTS_BY_NAME[key]


# --------------------------------------------------------------------------- detection


def _newest_mtime(root: Path, max_entries: int = 4000) -> float | None:
    """Newest mtime under root, walking breadth-first with a hard entry cap."""
    try:
        newest = root.stat().st_mtime
    except OSError:
        return None
    queue, seen = [root], 0
    while queue and seen < max_entries:
        current = queue.pop(0)
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    newest = max(newest, stat.st_mtime)
                    if entry.is_dir(follow_symlinks=False):
                        queue.append(Path(entry.path))
        except OSError:
            continue
    return newest


def detect(agent: Agent) -> Detection:
    binary = next((path for name in agent.binaries if (path := shutil.which(name))), None)
    times = [t for d in agent.activity_dirs if (t := _newest_mtime(d)) is not None]
    return Detection(agent=agent, binary=binary, logged_in=agent.logged_in(), last_used=max(times) if times else None)


def detect_all() -> list[Detection]:
    """Every known agent, most recently used first."""
    found = [detect(agent) for agent in AGENTS]
    return sorted(found, key=lambda d: (d.binary is not None, d.last_used or 0), reverse=True)


# --------------------------------------------------------------------------- running


def run_agent(detection: Detection, prompt: str, model: str | None = None) -> Generation:
    """Send one prompt to an agent CLI and parse its reply."""
    agent = detection.agent
    if detection.binary is None:
        raise GitCapError(f"{agent.label} is not installed.")
    chosen_model = model or agent.default_model
    args, stdin = agent.build(prompt, chosen_model)
    started = time.monotonic()
    # Run outside the repo so the agent doesn't load project memory files or crawl code:
    # everything it needs is already in the prompt.
    with tempfile.TemporaryDirectory(prefix="gitcap-") as workdir:
        try:
            proc = subprocess.run(
                [detection.binary, *args],
                input=stdin,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=AGENT_TIMEOUT_SECONDS,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCapError(f"{agent.label} timed out after {AGENT_TIMEOUT_SECONDS}s.") from exc
        except OSError as exc:
            raise GitCapError(f"{agent.label} could not be started: {exc.strerror or exc}") from exc

    if proc.returncode != 0:
        # Some CLIs report structured errors on stdout with a non-zero exit.
        try:
            agent.parse(proc.stdout)
        except GitCapError:
            raise
        except Exception:  # noqa: BLE001 - fall through to the stderr summary
            pass
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise GitCapError(f"{agent.label} exited with {proc.returncode}: {detail[-1] if detail else 'no output'}")

    generation = agent.parse(proc.stdout)
    generation.source = agent.name
    generation.model = generation.model or chosen_model
    generation.seconds = time.monotonic() - started
    return generation
