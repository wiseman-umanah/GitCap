import pytest

from gitcap import generate as gen_mod
from gitcap.config import GitCapConfig
from gitcap.errors import GitCapError, PromptTooLarge
from gitcap.generate import AgentsFailed, Candidate, NeedsGroqKey, candidates, generate
from gitcap.result import Generation
from tests.fakes import make_agent


def ok(text="feat: ok", source="x"):
    return lambda prompt: Generation(text=text, source=source)


def fail(message):
    def run(prompt):
        raise GitCapError(message)

    return run


def prompt_of(budget):
    return f"budget={budget}"


def test_first_success_wins_and_later_candidates_not_called():
    called = []
    chain = [
        Candidate("a", "A", 1000, ok("feat: from a")),
        Candidate("b", "B", 1000, lambda p: called.append(p) or Generation(text="x")),
    ]
    assert generate(chain, prompt_of).text == "feat: from a"
    assert called == []


def test_falls_back_and_reports_failures():
    failures = []
    chain = [Candidate("a", "A", 1000, fail("A exited with 1: logged out")), Candidate("b", "B", 1000, ok("feat: b"))]
    result = generate(chain, prompt_of, on_failure=lambda c, why: failures.append((c.name, why)))
    assert result.text == "feat: b"
    assert failures == [("a", "exited with 1: logged out")]  # label not repeated


def test_empty_reply_counts_as_failure():
    chain = [Candidate("a", "A", 1000, ok("```\n\n```")), Candidate("b", "B", 1000, ok("feat: b"))]
    assert generate(chain, prompt_of).text == "feat: b"


def test_output_is_cleaned():
    chain = [Candidate("a", "A", 1000, ok("<think>x</think>\n```\nfeat: clean\n```"))]
    assert generate(chain, prompt_of).text == "feat: clean"


def test_each_backend_gets_its_own_budget():
    seen = []

    def record(text):
        def run(prompt):
            seen.append(prompt)
            if text is None:
                raise GitCapError("nope")
            return Generation(text=text)

        return run

    chain = [Candidate("big", "Big", 100_000, record(None)), Candidate("small", "Small", 16_000, record("feat: x"))]
    generate(chain, prompt_of)
    assert seen == ["budget=100000", "budget=16000"]


def test_prompt_too_large_shrinks_by_reported_ratio():
    budgets = []

    def run(prompt):
        budgets.append(int(prompt.split("=")[1]))
        if len(budgets) == 1:
            raise PromptTooLarge("too big", ratio=0.6)
        return Generation(text="feat: fits")

    shrinks = []
    chain = [Candidate("g", "G", 16_000, run)]
    assert generate(chain, prompt_of, on_shrink=lambda c, b: shrinks.append(b)).text == "feat: fits"
    assert budgets == [16_000, 9_600]
    assert shrinks == [9_600]


def test_prompt_too_large_halves_without_ratio_then_gives_up():
    budgets = []

    def run(prompt):
        budgets.append(int(prompt.split("=")[1]))
        raise PromptTooLarge("too big")

    with pytest.raises(GitCapError, match="Stage fewer files"):
        generate([Candidate("g", "G", 16_000, run)], prompt_of)
    assert budgets == [16_000, 8_000, 4_000]


def test_shrink_never_below_minimum():
    budgets = []

    def run(prompt):
        budgets.append(int(prompt.split("=")[1]))
        raise PromptTooLarge("too big", ratio=0.01)

    with pytest.raises(GitCapError):
        generate([Candidate("g", "G", 5_000, run)], prompt_of)
    assert min(budgets) == gen_mod.MIN_BUDGET_CHARS


def test_all_agents_failed_is_distinguishable():
    with pytest.raises(AgentsFailed, match="Every backend failed"):
        generate([Candidate("a", "A", 1, fail("x")), Candidate("b", "B", 1, fail("y"))], prompt_of)


def test_groq_failure_is_not_agents_failed():
    with pytest.raises(GitCapError) as info:
        generate([Candidate("groq", "Groq", 1, fail("rate limit"))], prompt_of)
    assert not isinstance(info.value, AgentsFailed)


# --------------------------------------------------------------------------- chain building


@pytest.fixture
def isolated_agents(monkeypatch, tmp_path, bin_dir, env):
    """Registry with two fake agents whose files live under tmp_path."""
    from gitcap import agents

    def fake(name, auth):
        return agents.Agent(
            name=name, label=name.title(), binaries=(f"{name}-cli",),
            activity_dirs=(tmp_path / name,), auth_files=(tmp_path / f"{name}.auth",) if auth else (),
            build=agents.AGENTS_BY_NAME["claude"].build, parse=agents.parse_result_json,
        )

    registry = (fake("alpha", auth=True), fake("beta", auth=True))
    monkeypatch.setattr(agents, "AGENTS", registry)
    monkeypatch.setattr(agents, "AGENTS_BY_NAME", {a.name: a for a in registry})
    return tmp_path


def test_auto_chain_orders_by_recent_use_and_skips_logged_out(isolated_agents, bin_dir):
    import os
    import time

    tmp = isolated_agents
    for name in ("alpha", "beta"):
        make_agent(bin_dir, f"{name}-cli")
        (tmp / name).mkdir()
    (tmp / "beta.auth").write_text("{}")
    (tmp / "alpha.auth").write_text("{}")
    os.utime(tmp / "alpha", (time.time() - 9999,) * 2)
    assert [c.name for c in candidates(GitCapConfig())] == ["beta", "alpha"]

    (tmp / "beta.auth").unlink()
    assert [c.name for c in candidates(GitCapConfig())] == ["alpha"]


def test_auto_chain_appends_groq_when_key_set(isolated_agents, bin_dir):
    make_agent(bin_dir, "alpha-cli")
    (isolated_agents / "alpha.auth").write_text("{}")
    assert [c.name for c in candidates(GitCapConfig(groq_api_key="gsk"))] == ["alpha", "groq"]


def test_auto_with_nothing_needs_groq_key(isolated_agents):
    with pytest.raises(NeedsGroqKey, match="console.groq.com/keys"):
        candidates(GitCapConfig())


def test_explicit_groq_without_key(env):
    with pytest.raises(NeedsGroqKey):
        candidates(GitCapConfig(), backend="groq")


def test_explicit_agent_not_installed(isolated_agents):
    with pytest.raises(GitCapError, match="not installed"):
        candidates(GitCapConfig(), backend="alpha")


def test_explicit_agent_means_no_fallback(isolated_agents, bin_dir):
    make_agent(bin_dir, "alpha-cli")
    chain = candidates(GitCapConfig(groq_api_key="gsk"), backend="alpha")
    assert [c.name for c in chain] == ["alpha"]


def test_configured_model_ignored_in_auto_mode(isolated_agents, bin_dir, monkeypatch):
    from gitcap import agents

    seen = {}
    monkeypatch.setattr(agents, "run_agent", lambda d, p, m=None: seen.setdefault("model", m) or Generation(text="x"))
    make_agent(bin_dir, "alpha-cli")
    (isolated_agents / "alpha.auth").write_text("{}")
    candidates(GitCapConfig(model="sonnet"))[0].run("p")
    assert seen["model"] is None


def test_explicit_local_not_running(env):
    with pytest.raises(GitCapError, match="No local model server"):
        candidates(GitCapConfig(local_url="http://127.0.0.1:9"), backend="local")


def test_auto_includes_running_local_server_before_groq(isolated_agents, server):
    server.routes["GET /api/tags"] = lambda b: (200, {"models": [{"name": "qwen3:8b"}]})
    server.routes["POST /api/show"] = lambda b: (200, {"model_info": {"qwen3.context_length": 8192}})
    chain = candidates(GitCapConfig(local_url=server.url, groq_api_key="gsk"))
    assert [c.name for c in chain] == ["local", "groq"]
    assert chain[0].budget_chars == (8192 - 1500) * 3


# --------------------------------------------------------------------------- default agent + health


def _both_installed(tmp, bin_dir):
    import os
    import time

    for name in ("alpha", "beta"):
        make_agent(bin_dir, f"{name}-cli")
        (tmp / name).mkdir()
        (tmp / f"{name}.auth").write_text("{}")
    os.utime(tmp / "alpha", (time.time() - 9999,) * 2)  # beta used most recently


def test_default_agent_goes_first_and_rest_stay_as_fallback(isolated_agents, bin_dir):
    _both_installed(isolated_agents, bin_dir)
    chain = candidates(GitCapConfig(backend="alpha", groq_api_key="gsk"))
    assert [c.name for c in chain] == ["alpha", "beta", "groq"]


def test_default_agent_tried_even_if_login_unconfirmed(isolated_agents, bin_dir):
    _both_installed(isolated_agents, bin_dir)
    (isolated_agents / "alpha.auth").unlink()
    assert [c.name for c in candidates(GitCapConfig(backend="alpha"))] == ["alpha", "beta"]


def test_default_model_only_applies_to_default_agent(isolated_agents, bin_dir, monkeypatch):
    from gitcap import agents

    seen = []
    monkeypatch.setattr(agents, "run_agent", lambda d, p, m=None: seen.append((d.agent.name, m)) or Generation(text="x"))
    _both_installed(isolated_agents, bin_dir)
    for candidate in candidates(GitCapConfig(backend="alpha", model="big-model")):
        candidate.run("p")
    assert seen == [("alpha", "big-model"), ("beta", None)]


def test_model_without_any_backend_is_rejected(isolated_agents, bin_dir):
    _both_installed(isolated_agents, bin_dir)
    with pytest.raises(GitCapError, match="-a <agent>"):
        candidates(GitCapConfig(), model="sonnet")


def test_paused_backends_go_last_but_stay_in_chain():
    from gitcap.generate import order_by_health

    chain = [Candidate(n, n, 1, ok()) for n in ("a", "b", "c", "groq")]
    ordered = order_by_health(chain, {"a": (0, "credits"), "c": (0, "login")})
    assert [c.name for c in ordered] == ["b", "groq", "a", "c"]


def test_on_success_reports_the_winner():
    winners = []
    chain = [Candidate("a", "A", 1, fail("x")), Candidate("b", "B", 1, ok())]
    generate(chain, prompt_of, on_success=lambda c: winners.append(c.name))
    assert winners == ["b"]
