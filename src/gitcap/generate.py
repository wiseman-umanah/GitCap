"""Pick backends (logged-in agents, local models, Groq) and generate, falling back on failure."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gitcap import agents, groq, local
from gitcap.config import AUTO, GitCapConfig
from gitcap.errors import GitCapError, PromptTooLarge
from gitcap.result import Generation, clean_message

GROQ_KEYS_URL = "https://console.groq.com/keys"
MIN_BUDGET_CHARS = 2_000
MAX_SHRINKS = 2


class NeedsGroqKey(GitCapError):
    """No agent could be used and Groq has no key yet; the caller may offer Groq setup."""


class AgentsFailed(GitCapError):
    """Every backend in the chain failed and Groq wasn't part of it."""


@dataclass(slots=True)
class Candidate:
    name: str
    label: str
    budget_chars: int  # how much diff text this backend should be sent
    run: Callable[[str], Generation]


def _groq(config: GitCapConfig, model: str | None) -> Candidate:
    key = config.groq_key
    if not key:
        raise NeedsGroqKey(f"No Groq API key. Get a free one at {GROQ_KEYS_URL}, then run `gitcap config --groq-key <key>`.")
    return Candidate("groq", "Groq", groq.DIFF_BUDGET_CHARS, lambda prompt: groq.generate(prompt, key, model))


def _agent(detection: agents.Detection, model: str | None) -> Candidate:
    return Candidate(
        detection.agent.name,
        detection.agent.label,
        detection.agent.budget_chars,
        lambda prompt: agents.run_agent(detection, prompt, model),
    )


def _local(server: local.LocalServer, model: str | None) -> Candidate:
    chosen = local.pick_model(server, model)
    context = local.context_tokens(server, chosen)
    return Candidate(
        "local",
        f"{server.label} ({chosen})",
        local.budget_chars(context),
        lambda prompt: local.generate(server, prompt, chosen, context),
    )


def _only(config: GitCapConfig, choice: str, model: str | None) -> Candidate:
    """A single backend, used when one is asked for explicitly for this run."""
    if choice == "groq":
        return _groq(config, model)
    if choice == "local":
        server = local.detect(config.local_url)
        if server is None:
            raise GitCapError(
                "No local model server found (tried Ollama on :11434 and LM Studio on :1234).\n"
                "Start one, or point gitcap at yours with `gitcap config --local-url http://host:port`."
            )
        return _local(server, model)
    detection = agents.detect(agents.get_agent(choice))
    if detection.binary is None:
        raise GitCapError(f"{detection.agent.label} is not installed (looked for {', '.join(detection.agent.binaries)}).")
    return _agent(detection, model)


def candidates(config: GitCapConfig, backend: str | None = None, model: str | None = None) -> list[Candidate]:
    """Backends to try, in order.

    - `backend` (a one-off `-a`): that backend only, no fallback.
    - otherwise every available backend: your agents (most recently used first), a local model,
      then Groq. A default set with `gitcap config --agent X` goes first, and the rest stay as fallback.
    """
    if backend:
        return [_only(config, backend.lower(), model)]

    preferred = (config.backend or AUTO).lower()
    if model and preferred == AUTO:
        raise GitCapError("A model only makes sense for one backend. Add `-a <agent>`, or set a default with `gitcap config --agent`.")
    if preferred not in {AUTO, "groq", "local"}:
        preferred = agents.get_agent(preferred).name
    # A model setting belongs to the default backend, and to a one-off -a; never to the others.
    preferred_model = model or config.model

    def model_for(name: str) -> str | None:
        return preferred_model if name == preferred else None

    chain = [
        _agent(d, model_for(d.agent.name))
        for d in agents.detect_all()
        # The default agent is tried even when its login can't be confirmed from disk.
        if d.usable or (d.agent.name == preferred and d.binary is not None)
    ]
    server = local.detect(config.local_url)
    if server is not None:
        chain.append(_local(server, model_for("local")))
    if config.groq_key:
        chain.append(_groq(config, model_for("groq")))
    if not chain:
        raise NeedsGroqKey(
            "No logged-in coding agent or local model found, and no Groq key set.\n"
            "Log in to one of: " + ", ".join(a.name for a in agents.AGENTS) + ", start Ollama / LM Studio,\n"
            f"or get a free Groq key at {GROQ_KEYS_URL} and run `gitcap config --groq-key <key>`."
        )
    # Stable sort: the default backend first, everything else keeps its order.
    return sorted(chain, key=lambda c: c.name != preferred)


def order_by_health(chain: list[Candidate], paused: dict[str, tuple[float, str]]) -> list[Candidate]:
    """Move paused backends (expired plan, no credits, logged out) behind the healthy ones.

    They are kept, not dropped: if every healthy backend fails, they still get a try before gitcap gives up.
    """
    return sorted(chain, key=lambda c: c.name in paused)


def _run_with_budget(
    candidate: Candidate,
    make_prompt: Callable[[int], str],
    on_shrink: Callable[[Candidate, int], None] | None,
) -> Generation:
    """Run one backend, shrinking the diff and retrying if it says the prompt is too large."""
    budget = candidate.budget_chars
    for attempt in range(MAX_SHRINKS + 1):
        try:
            return candidate.run(make_prompt(budget))
        except PromptTooLarge as exc:
            if attempt == MAX_SHRINKS or budget <= MIN_BUDGET_CHARS:
                raise GitCapError(f"{exc} Stage fewer files and retry.") from None
            budget = max(int(budget * min(exc.ratio or 0.5, 0.9)), MIN_BUDGET_CHARS)
            if on_shrink:
                on_shrink(candidate, budget)
    raise AssertionError("unreachable")


def generate(
    chain: list[Candidate],
    make_prompt: Callable[[int], str],
    on_attempt: Callable[[Candidate], None] | None = None,
    on_failure: Callable[[Candidate, str], None] | None = None,
    on_shrink: Callable[[Candidate, int], None] | None = None,
    on_success: Callable[[Candidate], None] | None = None,
) -> Generation:
    """Try each candidate in order and return the first non-empty message.

    make_prompt(budget_chars) builds the prompt with the diff trimmed to that size.
    """
    failures: list[str] = []
    for candidate in chain:
        if on_attempt:
            on_attempt(candidate)
        try:
            generation = _run_with_budget(candidate, make_prompt, on_shrink)
        except GitCapError as exc:
            reason = str(exc).strip().splitlines()[0][:200]
            reason = reason.removeprefix(f"{candidate.label} ").strip()
            failures.append(f"{candidate.label}: {reason}")
            if on_failure:
                on_failure(candidate, reason)
            continue
        generation.text = clean_message(generation.text)
        if generation.text:
            if on_success:
                on_success(candidate)
            return generation
        failures.append(f"{candidate.label}: empty reply")
        if on_failure:
            on_failure(candidate, "empty reply")
    message = failures[0] if len(failures) == 1 else "Every backend failed:\n  " + "\n  ".join(failures)
    if all(c.name != "groq" for c in chain):
        raise AgentsFailed(message)
    raise GitCapError(message)


def groq_only(config: GitCapConfig, model: str | None = None) -> list[Candidate]:
    """A chain with just Groq, for use right after the user adds a key."""
    return [_groq(config, model)]
