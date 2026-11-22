from pathlib import Path

import pytest

from gitcap import git
from gitcap.errors import GitCapError
from tests.conftest import git as run_git


def test_repo_root_from_subdirectory(repo: Path):
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert git.repo_root(str(sub)).resolve() == repo.resolve()


def test_repo_root_outside_repo(tmp_path: Path, env):
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(GitCapError, match="Not inside a git repository"):
        git.repo_root(str(outside))


def test_repo_root_missing_path(tmp_path: Path, env):
    with pytest.raises(GitCapError, match="Not a directory"):
        git.repo_root(str(tmp_path / "nope"))


def test_nothing_staged(repo: Path):
    with pytest.raises(GitCapError, match="Nothing staged"):
        git.staged_changes(repo)


def test_staged_changes_reads_files(staged_repo: Path):
    changes = git.staged_changes(staged_repo)
    assert [path for path, _ in changes.files] == ["calc.py"]
    assert "calc.py" in changes.stat
    assert "+def sub(a, b):" in changes.render(10_000)


def test_lockfiles_are_listed_not_included(repo: Path):
    (repo / "app.py").write_text("print('hi')\n")
    (repo / "uv.lock").write_text("lock\n" * 500)
    (repo / "web").mkdir()
    (repo / "web" / "package-lock.json").write_text("{}\n")
    run_git(repo, "add", ".")
    changes = git.staged_changes(repo)
    assert sorted(changes.skipped) == ["uv.lock", "web/package-lock.json"]
    rendered = changes.render(50_000)
    assert "lock\nlock" not in rendered
    assert "content omitted): " in rendered


@pytest.mark.parametrize("setting", [("diff.mnemonicPrefix", "true"), ("diff.noprefix", "true")])
def test_user_diff_prefix_settings_do_not_break_parsing(repo: Path, setting):
    run_git(repo, "config", *setting)
    (repo / "uv.lock").write_text("x\n")
    (repo / "main.py").write_text("x = 1\n")
    run_git(repo, "add", ".")
    changes = git.staged_changes(repo)
    assert [p for p, _ in changes.files] == ["main.py"]
    assert changes.skipped == ["uv.lock"]


def test_render_respects_budget_and_names_every_file(repo: Path):
    for i in range(10):
        (repo / f"mod{i}.py").write_text("\n".join(f"line_{i}_{n} = {n}" for n in range(400)))
    run_git(repo, "add", ".")
    changes = git.staged_changes(repo)
    assert changes.full_size > 50_000
    for budget in (40_000, 8_000, 2_000):
        rendered = changes.render(budget)
        assert len(rendered) < budget * 1.2 + 1_000
        assert rendered.count("diff --git a/") == 10  # every file keeps its header
        assert rendered.count(" truncated)") == 10


def test_render_small_files_kept_whole_when_one_is_huge(repo: Path):
    (repo / "big.py").write_text("\n".join(f"x{n} = {n}" for n in range(5000)))
    (repo / "small.py").write_text("tiny = True\n")
    run_git(repo, "add", ".")
    rendered = git.staged_changes(repo).render(6_000)
    assert "+tiny = True" in rendered
    assert "(big.py truncated)" in rendered
    assert "(small.py truncated)" not in rendered


def test_recent_subjects(staged_repo: Path):
    assert git.recent_subjects(staged_repo) == ["chore: init"]


def test_recent_subjects_empty_repo(repo: Path):
    assert git.recent_subjects(repo) == []


def test_commit_uses_message_verbatim(staged_repo: Path):
    message = 'feat(calc): add sub\n\n- handles "quotes" and $dollars\n- second line'
    git.commit(staged_repo, message)
    assert run_git(staged_repo, "log", "-1", "--format=%B").strip() == message


def test_commit_nothing_staged(repo: Path):
    with pytest.raises(GitCapError):
        git.commit(repo, "feat: nothing")
