# gitcap

AI-powered git commit message generator that supports 
Groq, Claude, ChatGPT and OpenRouter.

## Installation

pip install gitcap

## Quick Start

Stage your changes then run:

gitcap commit .

## Providers

- Groq (free, fast)
- Claude (Anthropic)  
- ChatGPT (OpenAI)
- OpenRouter (multiple models)

## Commands

gitcap commit .              # generate and commit
gitcap commit . --dry-run    # preview without committing
gitcap commit . --auto       # commit immediately
gitcap commit . --context "hotfix for login"  # add context
gitcap config                # show current config
gitcap config --reset        # clear saved config
gitcap config --provider groq # switch provider

## License

MIT
