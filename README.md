# gitcap

[![CI](https://github.com/wiseman-umanah/GitCap/actions/workflows/ci.yml/badge.svg)](https://github.com/wiseman-umanah/GitCap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gitcap)](https://pypi.org/project/gitcap/)
[![Python](https://img.shields.io/pypi/pyversions/gitcap)](https://pypi.org/project/gitcap/)

**Commit messages, written by the agent you already pay for.**

gitcap hands your staged diff to the coding agent you're already logged in to (Claude Code, Codex, Gemini CLI,
Grok CLI, Kimi Code, Qoder CLI, OpenCode, Cursor Agent, Copilot CLI or Crush) and gives you back a clean
Conventional Commit. You don't need new API keys. When one agent's plan runs out, the next one takes over.
It can also use a local model (Ollama, LM Studio, llama.cpp), or Groq's free API as a last resort.

**Website:** <https://wiseman-umanah.github.io/GitCap/> · **Changelog:** [CHANGELOG.md](CHANGELOG.md)

## Installation

```sh
uv tool install gitcap     # or: pipx install gitcap / pip install gitcap
```

Python 3.11 to 3.13. Two small dependencies (`rich`, `readchar`), no AI SDKs.

## Quick start

```sh
git add -p
gitcap commit
```

```
╭─ commit message ───────────────────────────────────────────╮
│                                                            │
│  feat(calc): add subtraction and document add              │
│                                                            │
╰────────────────────────────────────────────────────────────╯
  claude · claude-haiku-5-5 · 4.1s   1.5k in · 40 out · $0.0003
c commit  e edit  r regenerate  q quit
```

Every message shows which agent and model wrote it, how long it took, and the exact token counts and cost
that agent reported.

## How gitcap picks an agent

gitcap reads only what's already on your machine: agent programs on your `PATH`, their login files and how
recently their session logs changed. It never opens your tokens. Each agent runs on its own login, so a
Claude, ChatGPT or Grok subscription pays for the message. API keys in your environment work too
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `MOONSHOT_API_KEY`, `CURSOR_API_KEY`).

The order it tries them in:

1. your default agent, if you set one with `gitcap config --agent`
2. your other agents, most recently used first
3. a local model server, if one is running
4. Groq, if you've added a key

If one fails, gitcap moves on to the next. It only fails when there's nothing left to try, and then it tells
you how to recover. If no agent is available and you haven't set a Groq key, gitcap points you to
[console.groq.com/keys](https://console.groq.com/keys) for a free one. You paste it in once and the commit
carries on.

### When a plan expires or credits run out

An agent that fails for an account reason (expired plan, no credits, logged out) is **paused for 6 hours**:
it moves to the back of the line, so you're not waiting for it to fail on every commit. Rate limits pause it
for 15 minutes. An agent that fails twice in a row for any other reason is paused for 30 minutes. Paused
agents are still tried before gitcap gives up, and the first success clears the pause.

```sh
gitcap agents            # every agent, the order they're tried in, and which are paused and why
gitcap agents --retry    # clear the pauses, e.g. right after you renew a plan
```

### Switching agents

```sh
gitcap config --agent grok    # try Grok first; your other agents stay as fallback
gitcap config --agent auto    # back to "most recently used first"
gitcap commit -a codex        # just this once, use only Codex (no fallback)
```

## Commands

```sh
gitcap commit                       # generate, review, commit (from any folder in the repo)
gitcap commit -n                    # dry run: print the message only
gitcap commit -y                    # commit without review
gitcap commit -c "hotfix for login" # extra context for the agent
gitcap commit -a grok               # only this agent, this once
gitcap commit -a claude -m sonnet   # ...and this model
gitcap commit -a local -m qwen3:8b  # a local model

gitcap agents                       # detected agents, their order, any pauses
gitcap agents --retry               # give paused agents another chance now

gitcap config                       # show settings
gitcap config --agent claude        # try this agent first (or: auto, local, groq)
gitcap config --model sonnet        # model for your default agent (`default` clears it)
gitcap config --groq-key gsk_...    # or set GROQ_API_KEY
gitcap config --local-url http://127.0.0.1:8080   # llama.cpp, vLLM, ... (or GITCAP_LOCAL_URL)
gitcap config --reset

gitcap upgrade                      # update to the latest release (uv tool, pipx or pip)
gitcap upgrade --check              # only say whether one is available
gitcap uninstall                    # remove gitcap, its settings (including the Groq key) and caches
gitcap uninstall --keep-config      # remove only the package
```

## Updates

On every run, gitcap checks PyPI for a newer release in the background while your command works. When one
exists, it prints a short notice at the end with the changelog link and `gitcap upgrade`.

The check never slows a command down. gitcap waits at most 0.4s for it, and doesn't wait at all if the last
answer is less than a day old. If PyPI is slow, a small detached process finishes the check and caches the
result for next time. No check runs in CI, when output is piped, or with `GITCAP_NO_UPDATE_CHECK=1`.

## How agents are run

Each agent gets one prompt in its non-interactive mode. It runs in an empty temporary folder with its tools
switched off wherever the CLI allows that, so it can't read or change your repo. The staged diff is sent
inside the prompt, along with your recent commit subjects so the message matches your repo's style.
Claude Code defaults to `haiku` because it's fast and cheap. Other agents use whatever model they're
configured with unless you pass `-m`.

## Large diffs and context limits

The diff is read once, then trimmed to fit each backend:

| Backend | Diff budget | Why |
| --- | --- | --- |
| Agents reading the prompt from stdin (Claude Code, Codex, Qoder, Crush) | ~100k chars | 200k+ token windows; the limit keeps cost and latency down |
| Agents taking the prompt as an argument | ~60k chars | Linux allows 128 KiB per argument |
| Groq | ~16k chars | The free tier allows 8K tokens per minute, and one request can't exceed that |
| Local models | sized from the model's real context window | e.g. 8K context leaves about 20k chars |

Trimming always keeps the `--stat` summary and every file's header, and shares the space fairly between
files, smallest first. Lockfile and generated-file contents are left out. If a backend still says the prompt
is too large, gitcap shrinks it and retries. For Groq it uses the exact limit reported in the error, and it
waits out short rate-limit pauses.

For Ollama, gitcap sets `num_ctx` explicitly, capped at 32K. Otherwise Ollama loads models with a small
default window and silently drops the start of longer prompts, which is where the instructions are.

## Files gitcap keeps

| Path | What |
| --- | --- |
| `~/.config/gitcap/config.json` | Settings and the Groq key, readable only by you (mode 0600) |
| `~/.cache/gitcap/update-check.json` | The latest version seen on PyPI |
| `~/.cache/gitcap/backend-health.json` | Paused agents and why |

`gitcap uninstall` removes all of them.

## Development

```sh
uv sync --group dev
uv run pytest            # no network, no real agents; the site tests need Node
uv run ruff check src tests
```

The tests use fake agent CLIs (small scripts that log their arguments and stdin), an in-process fake HTTP
server for Groq, Ollama, LM Studio and PyPI, and throwaway git repos. Every test runs with a temporary
`HOME`, config folder, cache folder and `PATH`, so your real agents, credentials and settings are never
touched.

CI (`.github/workflows/ci.yml`) runs on every push and PR:
- lint
- tests on Linux and macOS with Python 3.11–3.13
- a build check that installs the wheel in a clean environment and runs it

### Website

The site lives in `site/` (plain HTML, CSS and JavaScript, no build step). `.github/workflows/pages.yml`
deploys it to GitHub Pages whenever `site/` or `CHANGELOG.md` changes on master, with `CHANGELOG.md`
copied in. The page reads that file and renders the changelog section from it, so the site always matches
the latest release.

To preview it locally:

```sh
mkdir -p _site && cp -r site/. _site/ && cp CHANGELOG.md _site/
python -m http.server -d _site 8000
```

### Releasing

Releases are automated with [release-please](https://github.com/googleapis/release-please):

1. Merge to master as usual, with Conventional Commit messages (gitcap writes those for you).
2. A **release PR** stays open and updates itself after each push. It holds the next version, worked out
   from the commits (`feat` gives a minor bump, `fix` a patch, `!` a breaking change), and the
   `CHANGELOG.md` entries. It bumps the version in `pyproject.toml`, `src/gitcap/__init__.py` and `uv.lock`.
3. Merge the release PR when you want to ship. The same workflow run then tags `vX.Y.Z`, creates the
   GitHub release, runs the full CI, checks that the tag and versions match, and publishes to PyPI.
   The updated `CHANGELOG.md` also redeploys the website.

Pushing a `v*` tag by hand still publishes too.

## License

MIT
