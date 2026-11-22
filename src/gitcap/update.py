"""Update notice and self-upgrade.

Every run starts a background check against PyPI while the command does its work. At the end, gitcap
waits a moment at most for the answer, otherwise falls back to the last cached one, so the check
never makes a command slower.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from gitcap import __version__
from gitcap.errors import GitCapError

PACKAGE = "gitcap"
PYPI_URL = os.environ.get("GITCAP_PYPI_URL", f"https://pypi.org/pypi/{PACKAGE}/json")
RELEASES_URL = "https://github.com/wiseman-umanah/GitCap/releases"
FETCH_TIMEOUT = 3.0  # the background request may take this long...
EXIT_WAIT = 0.4  # ...but the command waits at most this much for it when it finishes
FRESH_SECONDS = 24 * 3600  # with a cached answer this recent, don't wait at all
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Plain X.Y.Z only; pre-releases and anything unusual are ignored."""
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_newer(latest: str | None, current: str = __version__) -> bool:
    new, old = parse_version(latest or ""), parse_version(current)
    return bool(new and old and new > old)


def cache_file() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / PACKAGE / "update-check.json"


def _cache() -> dict:
    try:
        data = json.loads(cache_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_cache() -> str | None:
    return _cache().get("latest")


def cache_is_fresh() -> bool:
    checked = _cache().get("checked_at")
    return isinstance(checked, (int, float)) and time.time() - checked < FRESH_SECONDS


def write_cache(latest: str) -> None:
    try:
        path = cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"latest": latest, "checked_at": int(time.time())}))
    except OSError:
        pass  # a read-only home must never break the tool


def fetch_latest(timeout: float = FETCH_TIMEOUT) -> str | None:
    request = urllib.request.Request(PYPI_URL, headers={"Accept": "application/json", "User-Agent": PACKAGE})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["info"]["version"]


def _stderr_is_terminal() -> bool:
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def refresh_cache() -> None:
    """Fetch the latest version and cache it. Entry point for the detached refresher."""
    try:
        latest = fetch_latest()
    except Exception:  # noqa: BLE001
        return
    if latest:
        write_cache(latest)


def refresh_in_background() -> None:
    """Finish a check in a detached process so a quick command can exit without waiting."""
    try:
        subprocess.Popen(
            [sys.executable, "-c", "from gitcap.update import refresh_cache; refresh_cache()"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass


def checks_enabled() -> bool:
    if os.environ.get("GITCAP_NO_UPDATE_CHECK") or os.environ.get("CI"):
        return False
    return _stderr_is_terminal()  # piped or scripted runs get no notice, so don't check either


class UpdateCheck:
    """A PyPI lookup running in a daemon thread."""

    def __init__(self) -> None:
        self.latest: str | None = None
        self._thread = threading.Thread(target=self._run, name="gitcap-update-check", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            latest = fetch_latest()
        except Exception:  # noqa: BLE001 - offline, PyPI down, bad JSON: stay silent
            return
        if latest:
            self.latest = latest
            write_cache(latest)

    def result(self, wait: float = EXIT_WAIT) -> str | None:
        # Long commands (commit) finish after the check has; quick ones (config, agents) shouldn't
        # wait for the network when yesterday's answer is good enough. The check still ran.
        if self._thread.is_alive() and not cache_is_fresh():
            self._thread.join(wait)
        if self._thread.is_alive():
            # Slow network: this process is about to exit and would kill the thread mid-request.
            refresh_in_background()
        return self.latest or read_cache()


def start_check() -> UpdateCheck | None:
    return UpdateCheck() if checks_enabled() else None


def notify(check: UpdateCheck | None) -> None:
    """Print the update box on stderr if a newer version is published."""
    if check is None:
        return
    latest = check.result()
    if not is_newer(latest):
        return
    from rich.console import Console
    from rich.panel import Panel

    body = (
        f"Update available! [red]{__version__}[/red] → [green bold]{latest}[/green bold]\n"
        f"Changelog: [cyan]{RELEASES_URL}/tag/v{latest}[/cyan]\n"
        f"Run [bold cyan]gitcap upgrade[/bold cyan] to update."
    )
    Console(stderr=True, highlight=False).print(Panel(body, border_style="yellow", padding=(1, 4), expand=False))


# --------------------------------------------------------------------------- self-upgrade


def install_method(prefix: str = sys.prefix) -> str:
    """How this copy of gitcap was installed: "uv", "pipx", "editable" or "pip"."""
    normalized = prefix.replace("\\", "/")
    if "/uv/tools/" in normalized:
        return "uv"
    if "/pipx/venvs/" in normalized:
        return "pipx"
    try:
        from importlib.metadata import distribution

        direct_url = distribution(PACKAGE).read_text("direct_url.json") or "{}"
        if json.loads(direct_url).get("dir_info", {}).get("editable"):
            return "editable"
    except Exception:  # noqa: BLE001 - metadata missing: treat as a normal install
        pass
    return "pip"


def upgrade_command(method: str) -> list[str]:
    if method == "uv":
        return ["uv", "tool", "upgrade", PACKAGE]
    if method == "pipx":
        return ["pipx", "upgrade", PACKAGE]
    return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]


def upgrade(check_only: bool = False) -> None:
    from gitcap import ui

    try:
        with ui.spinner("Checking PyPI…"):
            latest = fetch_latest(timeout=10)
    except Exception as exc:  # noqa: BLE001
        raise GitCapError(f"Could not reach PyPI: {exc}") from None
    if latest:
        write_cache(latest)
    if not is_newer(latest):
        ui.print(f"[green]✓[/green] gitcap {__version__} is the latest version.")
        return
    ui.print(f"Update available: [red]{__version__}[/red] → [green bold]{latest}[/green bold]  ([cyan]{RELEASES_URL}/tag/v{latest}[/cyan])")
    if check_only:
        return

    method = install_method()
    if method == "editable":
        raise GitCapError("This gitcap runs from a source checkout. Update it with `git pull` instead.")
    command = upgrade_command(method)
    ui.print(f"[dim]$ {' '.join(command)}[/dim]")
    try:
        result = subprocess.run(command)
    except FileNotFoundError:
        raise GitCapError(f"`{command[0]}` was not found. Run the upgrade yourself: {' '.join(command)}") from None
    if result.returncode != 0:
        raise GitCapError(f"Upgrade failed (exit {result.returncode}). See the output above.")
    ui.print(f"[green]✓[/green] Upgraded to gitcap {latest}.")
