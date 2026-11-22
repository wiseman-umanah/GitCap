"""Remember backends that failed for account reasons, so they stop slowing every run down.

When an agent fails because its subscription expired, the credits ran out or it's logged out,
it is paused for a while: it moves to the back of the line instead of being tried first each time.
Paused backends are still tried before gitcap gives up, and any success clears the pause.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ACCOUNT_PAUSE_SECONDS = 6 * 3600
RATE_LIMIT_PAUSE_SECONDS = 15 * 60
# Unrecognised errors (agents word things differently, and change it): pause after repeats.
REPEATED_FAILURES = 2
REPEATED_PAUSE_SECONDS = 30 * 60

# Signs that retrying soon won't help: the account itself is the problem.
_ACCOUNT_HINTS = (
    "credit", "quota", "billing", "subscription", "upgrade your", "plan limit", "usage limit",
    "payment", "insufficient", "expired", "not logged in", "logged out", "log in", "login",
    "sign in", "unauthorized", "unauthenticated", "authenticate", "authentication", "invalid api key",
    "api key is invalid", "forbidden", "401", "403",
)
_RATE_LIMIT_HINTS = ("rate limit", "rate-limit", "too many requests", "429", "overloaded")


def pause_seconds(reason: str) -> int | None:
    """How long to pause a backend after this failure, or None if it's not worth remembering."""
    text = reason.lower()
    if any(hint in text for hint in _ACCOUNT_HINTS):
        return ACCOUNT_PAUSE_SECONDS
    if any(hint in text for hint in _RATE_LIMIT_HINTS):
        return RATE_LIMIT_PAUSE_SECONDS
    return None  # timeouts, crashes, odd output: try it normally next time


def state_file() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "gitcap" / "backend-health.json"


def _load() -> dict[str, dict]:
    try:
        data = json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(data: dict[str, dict]) -> None:
    try:
        path = state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except OSError:
        pass  # never let bookkeeping break a commit


def paused(now: float | None = None) -> dict[str, tuple[float, str]]:
    """Backends currently paused: {name: (until_timestamp, reason)}."""
    now = time.time() if now is None else now
    return {
        name: (float(entry["until"]), str(entry.get("reason", "")))
        for name, entry in _load().items()
        if isinstance(entry.get("until"), (int, float)) and entry["until"] > now
    }


def record_failure(name: str, reason: str) -> int | None:
    """Count a failure and pause the backend when it's worth it. Returns the pause length, if any.

    Account problems (plan, credits, login) pause it at once; rate limits briefly; anything else
    only once it has failed REPEATED_FAILURES times in a row.
    """
    data = _load()
    entry = data.get(name, {})
    failures = int(entry.get("failures") or 0) + 1
    seconds = pause_seconds(reason)
    if seconds is None and failures >= REPEATED_FAILURES:
        seconds = REPEATED_PAUSE_SECONDS
    data[name] = {"failures": failures, "reason": reason[:160]}
    if seconds is not None:
        data[name]["until"] = time.time() + seconds
    _save(data)
    return seconds


def clear(name: str | None = None) -> None:
    """Clear one backend's pause, or every pause."""
    data = _load()
    if name is None:
        if data:
            _save({})
        return
    if data.pop(name, None) is not None:
        _save(data)
