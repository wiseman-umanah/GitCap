# Changelog

## [0.2.0](https://github.com/wiseman-umanah/GitCap/compare/v0.1.0...v0.2.0) (2026-11-22)


### ⚠ BREAKING CHANGES

* the OpenAI, Anthropic and OpenRouter API providers are removed; Groq is the only API backend. Settings moved from config.pkl to ~/.config/gitcap/config.json, so set the Groq key again with `gitcap config --groq-key`. `gitcap config --agent` sets the agent to try first, with the others kept as fallback.

### Features

* add update notices, `gitcap upgrade` and `gitcap uninstall` ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))
* **agents:** fall back to the next agent and pause ones whose plan expired ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))
* **diff:** trim the diff to fit each backend's context and retry smaller when a prompt is too large ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))
* **groq:** call Groq directly, swap in a live model when one is retired, and offer free-key setup when no agent is available ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))
* **local:** support local models through Ollama, LM Studio and llama.cpp ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))
* show the agent, model, time, token counts and cost for every message ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))
* write commit messages with your logged-in coding agents ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))


### Bug Fixes

* **git:** read diffs correctly with diff.mnemonicPrefix or diff.noprefix set ([2e225aa](https://github.com/wiseman-umanah/GitCap/commit/2e225aa18266dc7ccd523d386f7c987a11241890))


### Documentation

* rewrite the README and add CHANGELOG.md ([3fb9ef6](https://github.com/wiseman-umanah/GitCap/commit/3fb9ef6d0b823bffc6096791ceecc0254501b70b))
* **site:** add a project website on GitHub Pages ([8c90950](https://github.com/wiseman-umanah/GitCap/commit/8c90950ee349928ed2d18f8d5eb1649faa7da391))

## [0.1.0](https://github.com/wiseman-umanah/GitCap/commits/v0.1.0) (2026-04-29)


### Features

* initial `gitcap` command-line tool: generate a Conventional Commit message for staged changes with Groq, Claude, ChatGPT or OpenRouter ([e0f551c](https://github.com/wiseman-umanah/GitCap/commit/e0f551cf11af1bc1d65dbc38c264a9264336f741))
* **config:** update default models for Groq and add a diff character limit ([64ddfca](https://github.com/wiseman-umanah/GitCap/commit/64ddfca81528ab0aff48cdc06a357c210cb055c9))
* **pyproject:** add keywords, classifiers and project metadata ([b25271f](https://github.com/wiseman-umanah/GitCap/commit/b25271fa374b303f0656c4bbcff372c0071250b0), [8e46fdc](https://github.com/wiseman-umanah/GitCap/commit/8e46fdce6679223ce44fa07eef9e528b8b3416e7))


### Documentation

* **README:** add README documentation for gitcap ([8d44f8a](https://github.com/wiseman-umanah/GitCap/commit/8d44f8a42bdaeb1b71f34aba37e20a6c78a0e87a))
