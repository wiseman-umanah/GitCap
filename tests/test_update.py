import json
import sys
import time

import pytest

from gitcap import __version__, cli, update
from gitcap.errors import GitCapError

ROUTE = "GET /pypi/gitcap/json"


@pytest.fixture
def pypi(server, monkeypatch, env):
    monkeypatch.setattr(update, "PYPI_URL", server.url + "/pypi/gitcap/json")

    def publish(version, delay=0.0):
        def route(body):
            time.sleep(delay)
            return 200, {"info": {"version": version}}

        server.routes[ROUTE] = route

    return publish


@pytest.fixture
def interactive(monkeypatch):
    """Pretend stderr is a terminal and we're not in CI."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(update, "_stderr_is_terminal", lambda: True)


def bump(version: str) -> str:
    major, minor, patch = update.parse_version(version)
    return f"{major}.{minor + 1}.0"


# --------------------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("0.3.0", "0.2.0", True),
        ("0.2.1", "0.2.0", True),
        ("1.0.0", "0.9.9", True),
        ("0.10.0", "0.9.0", True),  # numeric, not string, comparison
        ("0.2.0", "0.2.0", False),
        ("0.1.9", "0.2.0", False),
        ("0.3.0rc1", "0.2.0", False),  # pre-releases never trigger a notice
        ("garbage", "0.2.0", False),
        (None, "0.2.0", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert update.is_newer(latest, current) is newer


# --------------------------------------------------------------------------- background check


def test_check_fetches_and_caches(pypi, interactive):
    pypi("9.9.9")
    check = update.start_check()
    assert check.result(wait=5) == "9.9.9"
    cached = json.loads(update.cache_file().read_text())
    assert cached["latest"] == "9.9.9" and cached["checked_at"] > 0


def test_slow_pypi_never_delays_the_command(pypi, interactive):
    update.write_cache("1.2.3")
    pypi("9.9.9", delay=2)
    check = update.start_check()
    started = time.monotonic()
    assert check.result() == "1.2.3"  # last known answer, not a wait
    assert time.monotonic() - started < update.EXIT_WAIT + 0.3


def test_fresh_cache_means_no_wait_but_check_still_runs(pypi, interactive, server, monkeypatch):
    monkeypatch.setattr(update, "refresh_in_background", lambda: None)  # the thread is enough here
    update.write_cache("1.2.3")  # fresh
    pypi("9.9.9", delay=0.3)
    check = update.start_check()
    started = time.monotonic()
    assert check.result() == "1.2.3"
    assert time.monotonic() - started < 0.1
    time.sleep(0.6)
    assert update.read_cache() == "9.9.9"  # refreshed in the background for next time
    assert len(server.requests) == 1


def test_stale_cache_waits_briefly_for_fresh_answer(pypi, interactive):
    update.cache_file().parent.mkdir(parents=True, exist_ok=True)
    update.cache_file().write_text(json.dumps({"latest": "1.2.3", "checked_at": time.time() - 3 * 86400}))
    pypi("9.9.9", delay=0.1)
    assert update.start_check().result() == "9.9.9"


def test_slow_check_is_handed_to_background_process(pypi, interactive, monkeypatch):
    handed_off = []
    monkeypatch.setattr(update, "refresh_in_background", lambda: handed_off.append(1))
    pypi("9.9.9", delay=1.5)
    update.start_check().result()
    assert handed_off == [1]


def test_fast_check_needs_no_background_process(pypi, interactive, monkeypatch):
    monkeypatch.setattr(update, "refresh_in_background", lambda: pytest.fail("not needed"))
    pypi("9.9.9")
    assert update.start_check().result(wait=5) == "9.9.9"


def test_background_refresher_writes_cache(pypi, env, monkeypatch):
    import subprocess

    pypi("7.7.7")
    subprocess.run(
        [sys.executable, "-c", "from gitcap.update import refresh_cache; refresh_cache()"],
        env={**env, "GITCAP_PYPI_URL": update.PYPI_URL}, timeout=30, check=True,
    )
    assert update.read_cache() == "7.7.7"


def test_offline_falls_back_to_cache(monkeypatch, interactive, env):
    monkeypatch.setattr(update, "PYPI_URL", "http://127.0.0.1:9/pypi/gitcap/json")
    update.write_cache("4.5.6")
    assert update.start_check().result(wait=2) == "4.5.6"


def test_offline_without_cache_is_silent(monkeypatch, interactive, env, capsys):
    monkeypatch.setattr(update, "PYPI_URL", "http://127.0.0.1:9/pypi/gitcap/json")
    update.notify(update.start_check())
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("var", ["CI", "GITCAP_NO_UPDATE_CHECK"])
def test_disabled_by_env(var, interactive, monkeypatch):
    monkeypatch.setenv(var, "1")
    assert update.start_check() is None


def test_disabled_when_not_a_terminal(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(update, "_stderr_is_terminal", lambda: False)
    assert update.start_check() is None


def test_unwritable_cache_is_ignored(monkeypatch, tmp_path, env):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))  # a file where a directory should be
    update.write_cache("1.0.0")
    assert update.read_cache() is None


# --------------------------------------------------------------------------- notice


def test_notice_shown_when_newer(pypi, interactive, capsys):
    latest = bump(__version__)
    pypi(latest)
    update.notify(update.start_check())
    err = capsys.readouterr().err
    assert "Update available!" in err and latest in err and "gitcap upgrade" in err
    assert f"/tag/v{latest}" in err


def test_no_notice_when_current(pypi, interactive, capsys):
    pypi(__version__)
    update.notify(update.start_check())
    assert capsys.readouterr().err == ""


def test_cli_shows_notice_after_command(pypi, interactive, capsys):
    pypi(bump(__version__))
    cli.main(["config"])
    captured = capsys.readouterr()
    assert "backend=auto" in captured.out
    assert "Update available!" in captured.err


def test_cli_shows_notice_after_errors_too(pypi, interactive, capsys, tmp_path):
    pypi(bump(__version__))
    with pytest.raises(SystemExit):
        cli.main(["commit", str(tmp_path)])  # not a repo
    assert "Update available!" in capsys.readouterr().err


# --------------------------------------------------------------------------- upgrade


@pytest.mark.parametrize(
    ("prefix", "method"),
    [
        ("/home/u/.local/share/uv/tools/gitcap", "uv"),
        ("/home/u/.local/share/pipx/venvs/gitcap", "pipx"),
        ("/home/u/.local/pipx/venvs/gitcap", "pipx"),
        ("C:\\Users\\u\\AppData\\Roaming\\uv\\tools\\gitcap", "uv"),
    ],
)
def test_install_method(prefix, method):
    assert update.install_method(prefix) == method


def test_upgrade_commands():
    assert update.upgrade_command("uv") == ["uv", "tool", "upgrade", "gitcap"]
    assert update.upgrade_command("pipx") == ["pipx", "upgrade", "gitcap"]
    assert update.upgrade_command("pip") == [sys.executable, "-m", "pip", "install", "--upgrade", "gitcap"]


def test_upgrade_runs_the_right_command(pypi, monkeypatch, capsys):
    pypi(bump(__version__))
    ran = []
    monkeypatch.setattr(update, "install_method", lambda: "uv")
    monkeypatch.setattr(update.subprocess, "run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0})())
    update.upgrade()
    assert ran == [["uv", "tool", "upgrade", "gitcap"]]
    assert "Upgraded to gitcap" in capsys.readouterr().out


def test_upgrade_check_only_does_not_install(pypi, monkeypatch, capsys):
    pypi(bump(__version__))
    monkeypatch.setattr(update.subprocess, "run", lambda cmd: pytest.fail("must not install"))
    update.upgrade(check_only=True)
    assert "Update available" in capsys.readouterr().out


def test_upgrade_when_current(pypi, capsys):
    pypi(__version__)
    update.upgrade()
    assert "is the latest version" in capsys.readouterr().out


def test_upgrade_refuses_source_checkout(pypi, monkeypatch):
    pypi(bump(__version__))
    monkeypatch.setattr(update, "install_method", lambda: "editable")
    with pytest.raises(GitCapError, match="git pull"):
        update.upgrade()


def test_upgrade_failure_is_reported(pypi, monkeypatch):
    pypi(bump(__version__))
    monkeypatch.setattr(update, "install_method", lambda: "pip")
    monkeypatch.setattr(update.subprocess, "run", lambda cmd: type("R", (), {"returncode": 1})())
    with pytest.raises(GitCapError, match="Upgrade failed"):
        update.upgrade()


def test_this_checkout_is_detected_as_editable():
    assert update.install_method() == "editable"  # tests run against `uv sync`'s editable install


# --------------------------------------------------------------------------- uninstall package step


def test_uninstall_commands():
    from gitcap.uninstall import uninstall_command

    assert uninstall_command("uv") == ["uv", "tool", "uninstall", "gitcap"]
    assert uninstall_command("pipx") == ["pipx", "uninstall", "gitcap"]
    assert uninstall_command("pip") == [sys.executable, "-m", "pip", "uninstall", "-y", "gitcap"]
    assert uninstall_command("editable") is None


def test_uninstall_runs_package_removal(env, monkeypatch, capsys):
    from gitcap import config, uninstall

    config.save_config(config.GitCapConfig(groq_api_key="gsk_secret"))
    ran = []
    monkeypatch.setattr(uninstall, "install_method", lambda: "uv")
    monkeypatch.setattr(uninstall.subprocess, "run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0})())
    uninstall.uninstall(yes=True)
    assert ran == [["uv", "tool", "uninstall", "gitcap"]]
    assert not config.config_dir().exists()  # the Groq key is gone too
    assert "gitcap is uninstalled" in capsys.readouterr().out


def test_uninstall_can_be_declined(env, monkeypatch):
    from gitcap import config, uninstall

    config.save_config(config.GitCapConfig())
    monkeypatch.setattr(uninstall.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(uninstall, "_confirm", lambda q: False)
    monkeypatch.setattr(uninstall.subprocess, "run", lambda cmd: pytest.fail("must not uninstall"))
    uninstall.uninstall()
    assert config.config_file().exists()
