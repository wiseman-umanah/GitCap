import json
import time

import pytest

from gitcap import health


@pytest.mark.parametrize(
    ("reason", "seconds"),
    [
        ("You've reached your credit usage limit. Please upgrade your subscription plan", health.ACCOUNT_PAUSE_SECONDS),
        ("exited with 1: Error: not logged in", health.ACCOUNT_PAUSE_SECONDS),
        ("Your subscription has expired", health.ACCOUNT_PAUSE_SECONDS),
        ("Groq API key is invalid. Run `gitcap config --groq-key <key>`.", health.ACCOUNT_PAUSE_SECONDS),
        ("insufficient_quota", health.ACCOUNT_PAUSE_SECONDS),
        ("HTTP 401 Unauthorized", health.ACCOUNT_PAUSE_SECONDS),
        ("Groq rate limit reached. Wait a bit and retry.", health.RATE_LIMIT_PAUSE_SECONDS),
        ("API overloaded (529)", health.RATE_LIMIT_PAUSE_SECONDS),
        ("timed out after 180s", None),
        ("exited with 139: segmentation fault", None),
        ("empty reply", None),
    ],
)
def test_pause_seconds(reason, seconds):
    assert health.pause_seconds(reason) == seconds


def test_record_pause_and_clear(env):
    assert health.paused() == {}
    assert health.record_failure("claude", "credit usage limit") == health.ACCOUNT_PAUSE_SECONDS
    assert health.record_failure("codex", "timed out after 180s") is None
    paused = health.paused()
    assert list(paused) == ["claude"]
    until, reason = paused["claude"]
    assert until > time.time() + health.ACCOUNT_PAUSE_SECONDS - 5
    assert reason == "credit usage limit"
    health.clear("claude")
    assert health.paused() == {}


def test_pauses_expire(env):
    health.record_failure("grok", "not logged in")
    assert "grok" not in health.paused(now=time.time() + health.ACCOUNT_PAUSE_SECONDS + 1)


def test_clear_all(env):
    health.record_failure("a", "quota")
    health.record_failure("b", "quota")
    health.clear()
    assert health.paused() == {}


def test_corrupt_state_is_ignored(env):
    health.state_file().parent.mkdir(parents=True, exist_ok=True)
    health.state_file().write_text("{nope")
    assert health.paused() == {}
    health.record_failure("a", "quota")  # rewrites a valid file
    assert json.loads(health.state_file().read_text())["a"]["reason"] == "quota"


def test_unwritable_state_never_raises(env, monkeypatch, tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    health.record_failure("a", "quota")
    assert health.paused() == {}


def test_unrecognised_errors_pause_after_repeats(env):
    reason = "Sorry, something went wrong. Please try again."  # Qoder's real error_code 500 text
    assert health.record_failure("qoder", reason) is None
    assert health.paused() == {}
    assert health.record_failure("qoder", reason) == health.REPEATED_PAUSE_SECONDS
    assert "qoder" in health.paused()


def test_success_resets_the_failure_count(env):
    health.record_failure("qoder", "something went wrong")
    health.clear("qoder")
    assert health.record_failure("qoder", "something went wrong") is None
