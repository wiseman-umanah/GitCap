import pytest

from gitcap.result import Usage, clean_message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("feat: add x", "feat: add x"),
        ("```\nfeat: add x\n```", "feat: add x"),
        ("```text\nfix(api): handle nulls\n\n- detail\n```", "fix(api): handle nulls\n\n- detail"),
        ("<think>reasoning\nmore</think>\nchore: bump", "chore: bump"),
        ("<thinking>x</thinking>docs: readme", "docs: readme"),
        ('"feat: quoted"', "feat: quoted"),
        ("`feat: backticked`", "feat: backticked"),
        ("  \n feat: padded \n ", "feat: padded"),
        ("", ""),
    ],
)
def test_clean_message(raw, expected):
    assert clean_message(raw) == expected


def test_clean_message_keeps_inner_quotes():
    assert clean_message('fix: handle "quoted" names') == 'fix: handle "quoted" names'


def test_usage_total():
    assert Usage(10, 5, 3, 2).total_tokens == 20
