"""Prompt construction for AI-generated commit messages."""

from __future__ import annotations


def build_commit_prompt(diff: str, context: str = "", recent: list[str] | None = None) -> str:
    """Build the prompt sent to whichever agent or API generates the message."""
    context_block = f"\nExtra context from the author:\n{context.strip()}\n" if context.strip() else ""
    style_block = ""
    if recent:
        examples = "\n".join(f"- {subject}" for subject in recent)
        style_block = f"\nRecent commit subjects in this repo (match their style and scopes):\n{examples}\n"
    return f"""Write one Conventional Commit message for the staged git diff below.

Rules:
- First line: type(scope): short imperative description, under 72 characters
- Types: feat, fix, perf, refactor, docs, style, test, build, ci, chore, revert
- Infer type and scope from the diff
- Add a blank line then short bullet points only when the change is non-trivial
- Describe only what is in the diff; never invent changes
- Output only the commit message: no preamble, no explanation, no markdown fences
- Do not use tools or read files; everything you need is below
{style_block}{context_block}
Staged changes:
{diff}
"""
