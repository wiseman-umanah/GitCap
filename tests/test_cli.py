"""End-to-end: run the real CLI in a subprocess with fake agents and a fake Groq."""

import json
from pathlib import Path

from tests.conftest import git
from tests.fakes import agent_calls, claude_json, make_agent

CHAT = "/openai/v1/chat/completions"


def logged_in_claude(home: Path, bin_dir: Path, **kwargs) -> None:
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    make_agent(bin_dir, "claude", **kwargs)


def groq_ok(server, text="feat: from groq"):
    server.routes[f"POST {CHAT}"] = lambda b: (200, {
        "model": b["model"], "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5},
    })
    server.routes["GET /openai/v1/models"] = lambda b: (200, {"data": [{"id": "openai/gpt-oss-120b"}]})


def test_version(run_cli):
    result = run_cli("--version")
    assert result.returncode == 0 and result.stdout.startswith("gitcap ")


def test_no_command_shows_help(run_cli):
    result = run_cli()
    assert result.returncode == 1 and "commit" in result.stdout


def test_dry_run_with_logged_in_agent(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat(calc): add sub"))
    result = run_cli("commit", str(staged_repo), "--dry-run")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "feat(calc): add sub" in result.stdout
    assert "claude" in result.stdout and "1.2k in" in result.stdout  # source + token usage shown
    assert "Dry run" in result.stdout
    assert git(staged_repo, "log", "--format=%s").splitlines() == ["chore: init"]
    [call] = agent_calls(bin_dir, "claude")
    assert "+def sub(a, b):" in call["stdin"]
    assert "chore: init" in call["stdin"]  # recent subjects as style examples


def test_yes_commits(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat(calc): add sub\n\n- adds sub()"))
    result = run_cli("commit", str(staged_repo), "--yes")
    assert result.returncode == 0, result.stdout
    assert git(staged_repo, "log", "-1", "--format=%B").strip() == "feat(calc): add sub\n\n- adds sub()"


def test_works_from_subdirectory(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat: x"))
    sub = staged_repo / "nested"
    sub.mkdir()
    assert run_cli("commit", str(sub), "-n").returncode == 0


def test_nothing_staged(run_cli, repo):
    result = run_cli("commit", str(repo), "-n")
    assert result.returncode == 1 and "Nothing staged" in result.stdout


def test_review_needs_a_terminal(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat: x"))
    result = run_cli("commit", str(staged_repo))
    assert result.returncode == 1 and "--yes or --dry-run" in result.stdout


def test_falls_back_to_groq_when_agent_fails(run_cli, staged_repo, home, bin_dir, server):
    logged_in_claude(home, bin_dir, exit_code=1, stderr="Error: not logged in\n")
    groq_ok(server)
    result = run_cli("commit", str(staged_repo), "-n", extra_env={"GROQ_API_KEY": "gsk_x", "GROQ_BASE_URL": server.url})
    assert result.returncode == 0, result.stdout
    assert "Claude Code failed" in result.stdout and "trying next" in result.stdout
    assert "feat: from groq" in result.stdout


def test_groq_shrinks_large_diff(run_cli, repo, server):
    for i in range(8):
        (repo / f"m{i}.py").write_text("\n".join(f"v{i}_{n} = {n}" for n in range(600)))
    git(repo, "add", ".")
    sizes = []

    def route(body):
        size = len(body["messages"][-1]["content"])
        sizes.append(size)
        if size > 9_000:
            return 413, {"error": {"message": f"Request too large: Limit 3000, Requested {size // 3}"}}
        return 200, {"model": body["model"], "choices": [{"message": {"content": "feat: many modules"}}], "usage": {}}

    server.routes[f"POST {CHAT}"] = route
    result = run_cli("commit", str(repo), "-n", "-a", "groq", extra_env={"GROQ_API_KEY": "k", "GROQ_BASE_URL": server.url})
    assert result.returncode == 0, result.stdout
    assert "retrying with" in result.stdout
    assert len(sizes) == 2 and sizes[1] < 9_000 < sizes[0]


def test_no_backend_non_interactive_explains_groq(run_cli, staged_repo):
    result = run_cli("commit", str(staged_repo), "-n")
    assert result.returncode == 1
    assert "console.groq.com/keys" in result.stdout


def test_explicit_agent_does_not_fall_back(run_cli, staged_repo, home, bin_dir, server):
    logged_in_claude(home, bin_dir, exit_code=1, stderr="nope\n")
    groq_ok(server)
    result = run_cli("commit", str(staged_repo), "-n", "-a", "claude",
                     extra_env={"GROQ_API_KEY": "k", "GROQ_BASE_URL": server.url})
    assert result.returncode == 1 and "from groq" not in result.stdout


def test_config_round_trip(run_cli, home, server):
    groq_ok(server)
    env = {"GROQ_BASE_URL": server.url}
    assert run_cli("config", "--agent", "Claude-Code", extra_env=env).returncode == 0
    assert run_cli("config", "--model", "sonnet", extra_env=env).returncode == 0
    assert run_cli("config", "--groq-key", "gsk_abc", extra_env=env).returncode == 0
    saved = json.loads((home / ".config" / "gitcap" / "config.json").read_text())
    assert saved == {"backend": "claude", "model": "sonnet", "groq_api_key": "gsk_abc"}
    shown = run_cli("config", extra_env=env).stdout
    assert "backend=claude" in shown and "gsk_abc" not in shown
    assert run_cli("config", "--reset").returncode == 0
    assert not (home / ".config" / "gitcap" / "config.json").exists()


def test_config_rejects_bad_groq_key(run_cli, home, server):
    server.routes["GET /openai/v1/models"] = lambda b: (401, {"error": {"message": "invalid"}})
    result = run_cli("config", "--groq-key", "bad", extra_env={"GROQ_BASE_URL": server.url})
    assert result.returncode == 1
    assert not (home / ".config" / "gitcap" / "config.json").exists()


def test_config_unknown_agent(run_cli):
    result = run_cli("config", "--agent", "nope")
    assert result.returncode == 1 and "Unknown agent" in result.stdout


def _row(output: str, label: str) -> str:
    return next(line for line in output.splitlines() if label in line)


def test_agents_table(run_cli, home, bin_dir):
    logged_in_claude(home, bin_dir)
    make_agent(bin_dir, "grok")  # installed, but no ~/.grok/auth.json
    result = run_cli("agents")
    assert result.returncode == 0
    assert _row(result.stdout, "Claude Code").split()[0] == "1"  # tried first
    grok = _row(result.stdout, "Grok CLI")
    assert grok.split()[0] == "·" and "logged out" in grok  # not in the chain
    assert "not installed" in _row(result.stdout, "Codex")


# --------------------------------------------------------------------------- expired plans, switching


CREDITS = "You've reached your credit usage limit. Please upgrade your subscription plan."


def logged_in_grok(home: Path, bin_dir: Path, **kwargs) -> None:
    (home / ".grok").mkdir(exist_ok=True)
    (home / ".grok" / "auth.json").write_text("{}")
    make_agent(bin_dir, "grok", **kwargs)


def make_claude_most_recent(home: Path) -> None:
    session = home / ".claude" / "projects" / "p"
    session.mkdir(parents=True, exist_ok=True)
    (session / "s.jsonl").write_text("{}")


def test_expired_agent_hands_over_and_is_skipped_next_time(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("", is_error=True, errors=[CREDITS]), exit_code=1)
    make_claude_most_recent(home)
    logged_in_grok(home, bin_dir, stdout=claude_json("feat: from grok", model="grok-4"))

    first = run_cli("commit", str(staged_repo), "-n")
    assert first.returncode == 0, first.stdout
    assert "Claude Code failed" in first.stdout and "skipping it for 6h" in first.stdout
    assert "feat: from grok" in first.stdout
    assert len(agent_calls(bin_dir, "claude")) == 1

    second = run_cli("commit", str(staged_repo), "-n")
    assert second.returncode == 0
    assert "Claude Code failed" not in second.stdout
    assert len(agent_calls(bin_dir, "claude")) == 1  # not tried again: Grok went first
    assert len(agent_calls(bin_dir, "grok")) == 2

    table = run_cli("agents").stdout
    assert "paused 6h" in table or "paused 5h" in table
    assert _row(table, "Grok CLI").split()[0] == "1"
    assert _row(table, "Claude Code").split()[0] == "2"


def test_paused_agent_still_tried_when_everything_else_fails(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat: claude is back"))
    make_claude_most_recent(home)
    logged_in_grok(home, bin_dir, exit_code=1, stderr="Error: not logged in\n")
    cache = home / ".cache" / "gitcap"
    cache.mkdir(parents=True)
    import time
    (cache / "backend-health.json").write_text(json.dumps({"claude": {"until": time.time() + 3600, "reason": "credits"}}))

    result = run_cli("commit", str(staged_repo), "-n")
    assert result.returncode == 0, result.stdout
    assert "Grok CLI failed" in result.stdout
    assert "feat: claude is back" in result.stdout
    assert "claude" not in json.loads((cache / "backend-health.json").read_text())  # success clears the pause


def test_fails_only_when_every_agent_is_used_up(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("", is_error=True, errors=[CREDITS]), exit_code=1)
    logged_in_grok(home, bin_dir, exit_code=1, stderr="Error: subscription expired\n")
    result = run_cli("commit", str(staged_repo), "-n")
    assert result.returncode == 1
    assert "Every backend failed" in result.stdout
    assert "Claude Code" in result.stdout and "Grok CLI" in result.stdout
    assert "log back in" in result.stdout and "console.groq.com/keys" in result.stdout


def test_default_agent_falls_back_when_it_fails(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat: claude"))
    logged_in_grok(home, bin_dir, exit_code=1, stderr="Error: not logged in\n")
    assert run_cli("config", "--agent", "grok").returncode == 0
    result = run_cli("commit", str(staged_repo), "-n")
    assert result.returncode == 0, result.stdout
    assert "Grok CLI failed" in result.stdout and "feat: claude" in result.stdout
    assert agent_calls(bin_dir, "grok")  # the default really went first


def test_one_off_agent_still_means_only_that_agent(run_cli, staged_repo, home, bin_dir):
    logged_in_claude(home, bin_dir, stdout=claude_json("feat: claude"))
    logged_in_grok(home, bin_dir, exit_code=1, stderr="Error: not logged in\n")
    result = run_cli("commit", str(staged_repo), "-n", "-a", "grok")
    assert result.returncode == 1 and "feat: claude" not in result.stdout
    assert "drop `-a grok`" in result.stdout
    assert not agent_calls(bin_dir, "claude")


def test_agents_retry_clears_pauses(run_cli, home, bin_dir):
    logged_in_claude(home, bin_dir)
    cache = home / ".cache" / "gitcap"
    cache.mkdir(parents=True)
    import time
    (cache / "backend-health.json").write_text(json.dumps({"claude": {"until": time.time() + 3600, "reason": "credits"}}))
    assert "paused" in run_cli("agents").stdout
    result = run_cli("agents", "--retry")
    assert "Cleared" in result.stdout
    assert "paused" not in _row(result.stdout, "Claude Code")


# --------------------------------------------------------------------------- uninstall


def test_uninstall_needs_confirmation_without_terminal(run_cli, home):
    run_cli("config", "--agent", "claude")
    result = run_cli("uninstall")
    assert result.returncode == 1 and "--yes" in result.stdout
    assert (home / ".config" / "gitcap" / "config.json").exists()


def test_uninstall_removes_settings_and_caches(run_cli, home):
    run_cli("config", "--agent", "claude")
    (home / ".cache" / "gitcap").mkdir(parents=True)
    (home / ".cache" / "gitcap" / "update-check.json").write_text("{}")
    result = run_cli("uninstall", "--yes")
    assert result.returncode == 0, result.stdout
    assert not (home / ".config" / "gitcap").exists()
    assert not (home / ".cache" / "gitcap").exists()
    assert "source checkout" in result.stdout  # tests run from an editable install: package left alone


def test_uninstall_keep_config(run_cli, home):
    run_cli("config", "--agent", "claude")
    result = run_cli("uninstall", "--yes", "--keep-config")
    assert (home / ".config" / "gitcap" / "config.json").exists()
    assert "Nothing to remove" in result.stdout
