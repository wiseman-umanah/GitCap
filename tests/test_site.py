"""The website's changelog parser (site/changelog.js), run in Node against real CHANGELOG.md content."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HARNESS = """
const fs = require("fs");
require(process.argv[1]);
const { parseChangelog, inline } = globalThis.gitcapChangelog;
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({
  releases: input.markdown === null ? null : parseChangelog(input.markdown),
  inline: input.inline.map(inline),
}));
"""

RELEASE_PLEASE_ENTRY = """# Changelog

## [0.3.0](https://github.com/wiseman-umanah/GitCap/compare/v0.2.0...v0.3.0) (2026-12-01)


### ⚠ BREAKING CHANGES

* **config:** `--agent` now sets a preference instead of the only agent

### Features

* **agents:** pause agents whose plan expired ([abc1234](https://github.com/wiseman-umanah/GitCap/commit/abc1234))
* add `gitcap uninstall` ([def5678](https://github.com/wiseman-umanah/GitCap/commit/def5678))


### Bug Fixes

* handle diff.mnemonicPrefix ([0a1b2c3](https://github.com/wiseman-umanah/GitCap/commit/0a1b2c3))

## 0.2.0 (2026-11-22)

### Features

* plain heading without a link
"""


def run_parser(markdown: str | None, inline: list[str] | None = None) -> dict:
    result = subprocess.run(
        [NODE, "-e", HARNESS, str(ROOT / "site" / "changelog.js")],
        input=json.dumps({"markdown": markdown, "inline": inline or []}),
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_parses_the_real_changelog():
    releases = run_parser((ROOT / "CHANGELOG.md").read_text())["releases"]
    assert releases, "CHANGELOG.md has no releases the site can show"
    oldest = releases[-1]
    assert oldest["version"] == "0.1.0"
    assert oldest["date"] == "2026-04-29"
    assert {s["title"] for s in oldest["sections"]} >= {"Features"}
    assert all(s["items"] for s in oldest["sections"])


def test_parses_release_please_entries_newest_first():
    releases = run_parser(RELEASE_PLEASE_ENTRY)["releases"]
    assert [r["version"] for r in releases] == ["0.3.0", "0.2.0"]
    latest = releases[0]
    assert latest["url"].endswith("compare/v0.2.0...v0.3.0")
    assert latest["date"] == "2026-12-01"
    titles = [(s["title"], s["breaking"]) for s in latest["sections"]]
    assert titles == [("BREAKING CHANGES", True), ("Features", False), ("Bug Fixes", False)]
    assert len(latest["sections"][1]["items"]) == 2
    assert releases[1]["url"] is None  # "## 0.2.0 (date)" without a link


def test_inline_formatting():
    [html] = run_parser(None, ["**agents:** add `gitcap uninstall` ([abc1234](https://github.com/x/y/commit/abc1234))"])["inline"]
    assert "<strong>agents:</strong>" in html
    assert "<code>gitcap uninstall</code>" in html
    assert '<a href="https://github.com/x/y/commit/abc1234" rel="noopener">abc1234</a>' in html


@pytest.mark.parametrize(
    "hostile",
    [
        "<img src=x onerror=alert(1)>",
        "[click](javascript:alert(1))",
        '[x](https://ok.example/" onmouseover="alert(1))',
        "`<script>alert(1)</script>`",
    ],
)
def test_inline_never_injects_markup(hostile):
    [html] = run_parser(None, [hostile])["inline"]
    assert "<img" not in html and "<script" not in html
    assert "javascript:" not in html.split("href=")[-1] if "href=" in html else True
    assert 'onmouseover="' not in html


def test_page_loads_parser_before_app():
    html = (ROOT / "site" / "index.html").read_text()
    assert html.index('src="changelog.js"') < html.index('src="app.js"')
