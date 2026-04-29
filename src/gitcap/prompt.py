"""Prompt construction for AI-generated commit messages."""

from __future__ import annotations


def build_commit_prompt(diff: str, context: str = "") -> str:
    """Build the provider prompt for a staged git diff."""
    context_block = f"\nAdditional user context:\n{context.strip()}\n" if context.strip() else ""
    return f"""Analyze this staged git diff and write one Conventional Commit message.

Rules:
- Format: type(scope): short description
- Allowed types: feat, fix, refactor, docs, style, test, chore
- Infer the most accurate type and scope from the diff
- Keep the first line under 72 characters
- Add bullet point body lines only when the changes are complex
- Never invent changes that are not present in the diff
- Return only the commit message, with no explanation or markdown fences
{context_block}
Staged git diff:
{diff}
"""
