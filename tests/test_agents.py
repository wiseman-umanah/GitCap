import json
import os
import time
from pathlib import Path

import pytest

from gitcap import agents
from gitcap.errors import GitCapError
from tests.fakes import agent_calls, claude_json, make_agent


def _detection(agent: agents.Agent, binary: Path) -> agents.Detection:
    return agents.Detection(agent=agent, binary=str(binary), logged_in=True, last_used=None)


# --------------------------------------------------------------------------- parsers


def test_parse_claude_result_with_usage():
    gen = agents.parse_result_json(claude_json("feat: x"))
    assert gen.text == "feat: x"
    assert gen.model == "claude-haiku-5-5"
    assert (gen.usage.input_tokens, gen.usage.output_tokens) == (1200, 30)
    assert (gen.usage.cache_read_tokens, gen.usage.cache_write_tokens) == (600, 900)
    assert gen.usage.cost_usd == pytest.approx(0.0003)


def test_parse_result_error_is_raised():
    with pytest.raises(GitCapError, match="credit usage limit"):
        agents.parse_result_json(claude_json("", is_error=True, errors=["You've reached your credit usage limit."]))


def test_parse_result_ignores_synthetic_model():
    out = json.dumps({"type": "result", "result": "x", "modelUsage": {"<synthetic>": {"inputTokens": 0}}})
    assert agents.parse_result_json(out).model is None


def test_parse_grok_text_field():
    out = json.dumps({"text": "fix: y", "usage": {"input_tokens": 70, "output_tokens": 4, "cache_read_input_tokens": 10}})
    gen = agents.parse_result_json(out)
    assert gen.text == "fix: y"
    assert (gen.usage.input_tokens, gen.usage.output_tokens, gen.usage.cache_read_tokens) == (70, 4, 10)


def test_parse_result_json_lines_picks_result_event():
    lines = "\n".join([json.dumps({"type": "system"}), "noise", claude_json("feat: z")])
    assert agents.parse_result_json(lines).text == "feat: z"


def test_parse_non_json_falls_back_to_text():
    assert agents.parse_result_json("feat: plain\n").text == "feat: plain"


def test_parse_codex_jsonl():
    events = [
        {"type": "thread.started"},
        {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "feat: codex"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 20}},
    ]
    gen = agents.parse_codex_jsonl("\n".join(json.dumps(e) for e in events))
    assert gen.text == "feat: codex"
    assert (gen.usage.input_tokens, gen.usage.cache_read_tokens, gen.usage.output_tokens) == (600, 400, 20)


def test_parse_codex_error():
    with pytest.raises(GitCapError, match="not logged in"):
        agents.parse_codex_jsonl(json.dumps({"type": "error", "message": "not logged in"}))


def test_parse_gemini_json():
    out = json.dumps({
        "response": "docs: gemini",
        "stats": {"models": {"gemini-3-flash": {"tokens": {"prompt": 900, "cached": 100, "candidates": 15, "thoughts": 5}}}},
    })
    gen = agents.parse_gemini_json(out)
    assert gen.text == "docs: gemini"
    assert gen.model == "gemini-3-flash"
    assert (gen.usage.input_tokens, gen.usage.cache_read_tokens, gen.usage.output_tokens) == (800, 100, 20)


def test_parse_gemini_error():
    with pytest.raises(GitCapError, match="quota"):
        agents.parse_gemini_json(json.dumps({"error": {"message": "quota exceeded"}}))


# --------------------------------------------------------------------------- argv builders


def test_claude_runs_without_tools_or_project_settings():
    args, stdin = agents.AGENTS_BY_NAME["claude"].build("PROMPT", "haiku")
    assert stdin == "PROMPT"  # large prompts go through stdin, not argv
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in args
    assert args[args.index("--model") + 1] == "haiku"


def test_codex_uses_read_only_sandbox_and_stdin():
    args, stdin = agents.AGENTS_BY_NAME["codex"].build("PROMPT", None)
    assert args[0] == "exec" and args[-1] == "-"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--model" not in args
    assert stdin == "PROMPT"


@pytest.mark.parametrize("name", [a.name for a in agents.AGENTS])
def test_every_agent_puts_prompt_somewhere(name):
    args, stdin = agents.AGENTS_BY_NAME[name].build("UNIQUE_PROMPT", None)
    assert stdin == "UNIQUE_PROMPT" or "UNIQUE_PROMPT" in args


@pytest.mark.parametrize("name", [a.name for a in agents.AGENTS])
def test_argv_prompts_fit_linux_argument_limit(name):
    agent = agents.AGENTS_BY_NAME[name]
    _, stdin = agent.build("x", None)
    if stdin is None:
        assert agent.budget_chars <= 100_000  # MAX_ARG_STRLEN is 128 KiB, leave room for instructions


def test_get_agent_aliases():
    assert agents.get_agent("Claude-Code").name == "claude"
    assert agents.get_agent("qodercli").name == "qoder"
    assert agents.get_agent("kimi-cli").name == "kimi"
    with pytest.raises(GitCapError, match="Unknown agent"):
        agents.get_agent("nope")


# --------------------------------------------------------------------------- detection


def _agent(tmp_path: Path, **overrides) -> agents.Agent:
    defaults = dict(
        name="fake",
        label="Fake Agent",
        binaries=("fake-agent",),
        activity_dirs=(tmp_path / "sessions",),
        auth_files=(tmp_path / "auth.json",),
        build=agents.AGENTS_BY_NAME["claude"].build,
        parse=agents.parse_result_json,
    )
    return agents.Agent(**{**defaults, **overrides})


def test_detect_not_installed(tmp_path, env):
    d = agents.detect(_agent(tmp_path))
    assert d.binary is None and not d.usable


def test_detect_installed_logged_out(tmp_path, env, bin_dir):
    make_agent(bin_dir, "fake-agent")
    d = agents.detect(_agent(tmp_path))
    assert d.binary and d.logged_in is False and not d.usable


def test_detect_logged_in_via_file_and_activity(tmp_path, env, bin_dir):
    make_agent(bin_dir, "fake-agent")
    (tmp_path / "auth.json").write_text("{}")
    session = tmp_path / "sessions" / "proj" / "s.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}")
    stamp = time.time() - 3600
    os.utime(session, (stamp, stamp))
    os.utime(session.parent, (stamp - 10, stamp - 10))
    os.utime(tmp_path / "sessions", (stamp - 20, stamp - 20))
    d = agents.detect(_agent(tmp_path))
    assert d.usable and d.logged_in is True
    assert d.last_used == pytest.approx(stamp, abs=1)


def test_detect_logged_in_via_env(tmp_path, env, bin_dir, monkeypatch):
    make_agent(bin_dir, "fake-agent")
    monkeypatch.setenv("FAKE_KEY", "1")
    assert agents.detect(_agent(tmp_path, auth_env=("FAKE_KEY",))).logged_in is True


def test_detect_unknown_login_is_still_usable(tmp_path, env, bin_dir):
    make_agent(bin_dir, "fake-agent")
    d = agents.detect(_agent(tmp_path, auth_files=()))
    assert d.logged_in is None and d.usable


# --------------------------------------------------------------------------- running


def test_run_agent_success_runs_outside_repo(tmp_path, env, bin_dir, staged_repo):
    binary = make_agent(bin_dir, "claude", stdout=claude_json("feat: from fake"))
    gen = agents.run_agent(_detection(agents.AGENTS_BY_NAME["claude"], binary), "THE PROMPT")
    assert gen.text == "feat: from fake"
    assert gen.source == "claude"
    [call] = agent_calls(bin_dir, "claude")
    assert call["stdin"] == "THE PROMPT"
    assert Path(call["cwd"]).resolve() != staged_repo.resolve()
    assert "gitcap-" in call["cwd"]
    assert not Path(call["cwd"]).exists()  # temp dir cleaned up


def test_run_agent_default_and_override_model(env, bin_dir):
    binary = make_agent(bin_dir, "claude", stdout=claude_json("feat: x"))
    detection = _detection(agents.AGENTS_BY_NAME["claude"], binary)
    agents.run_agent(detection, "p")
    agents.run_agent(detection, "p", model="sonnet")
    first, second = agent_calls(bin_dir, "claude")
    assert first["argv"][first["argv"].index("--model") + 1] == "haiku"
    assert second["argv"][second["argv"].index("--model") + 1] == "sonnet"


def test_run_agent_nonzero_exit_reports_stderr(env, bin_dir):
    binary = make_agent(bin_dir, "grok", exit_code=2, stderr="boom: not authenticated\n")
    with pytest.raises(GitCapError, match="exited with 2: boom: not authenticated"):
        agents.run_agent(_detection(agents.AGENTS_BY_NAME["grok"], binary), "p")


def test_run_agent_structured_error_on_stdout(env, bin_dir):
    out = claude_json("", is_error=True, errors=["credit usage limit"])
    binary = make_agent(bin_dir, "qodercli", stdout=out, exit_code=1)
    with pytest.raises(GitCapError, match="credit usage limit"):
        agents.run_agent(_detection(agents.AGENTS_BY_NAME["qoder"], binary), "p")


def test_run_agent_timeout(env, bin_dir, monkeypatch):
    monkeypatch.setattr(agents, "AGENT_TIMEOUT_SECONDS", 0.5)
    binary = make_agent(bin_dir, "crush", stdout="feat: late", sleep=5)
    with pytest.raises(GitCapError, match="timed out"):
        agents.run_agent(_detection(agents.AGENTS_BY_NAME["crush"], binary), "p")


def test_run_agent_not_installed():
    detection = agents.Detection(agent=agents.AGENTS_BY_NAME["claude"], binary=None, logged_in=None, last_used=None)
    with pytest.raises(GitCapError, match="not installed"):
        agents.run_agent(detection, "p")
