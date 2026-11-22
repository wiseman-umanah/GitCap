"""gitcap: commit messages from the coding agent you're already logged in to."""

__version__ = "0.2.0"  # x-release-please-version


def main() -> None:
    from gitcap.cli import main as run

    run()
