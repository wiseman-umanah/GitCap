// Parses release-please's CHANGELOG.md into releases, and renders its inline markdown safely.
// Kept separate from app.js so tests can run it in Node without a browser (tests/test_site.py).
(function (global) {
  "use strict";

  const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Inline markdown subset used by release-please: links, **bold**, `code`. Escape first, then format,
  // and only allow http(s) links, so nothing in the file can inject markup.
  function inline(text) {
    const codes = [];
    let html = escapeHtml(text).replace(/`([^`]+)`/g, (_, code) => {
      codes.push(code);
      return `\u0000${codes.length - 1}\u0000`;
    });
    html = html
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${url}" rel="noopener">${label}</a>`)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
  }

  // Parse release-please's CHANGELOG.md:
  //   ## [0.2.0](compare-url) (2026-11-22)   or   ## 0.2.0 (2026-11-22)
  //   ### Features
  //   * **scope:** description ([sha](url))
  function parseChangelog(markdown) {
    const releases = [];
    let release = null;
    let section = null;
    for (const raw of markdown.split(/\r?\n/)) {
      const line = raw.trimEnd();
      const heading = line.match(/^##\s+(?:\[([^\]]+)\]\(([^)]+)\)|(\S+))(?:\s+\((\d{4}-\d{2}-\d{2})\))?/);
      if (heading && !line.startsWith("###")) {
        release = { version: heading[1] || heading[3], url: heading[2] || null, date: heading[4] || "", sections: [] };
        releases.push(release);
        section = null;
        continue;
      }
      if (!release) continue;
      const sub = line.match(/^###\s+(.+)/);
      if (sub) {
        section = { title: sub[1].replace(/^⚠\s*/, ""), breaking: /breaking/i.test(sub[1]), items: [] };
        release.sections.push(section);
        continue;
      }
      const item = line.match(/^\s*[*-]\s+(.+)/);
      if (item) {
        if (!section) { section = { title: "Changes", breaking: false, items: [] }; release.sections.push(section); }
        section.items.push(item[1]);
      }
    }
    return releases;
  }

  global.gitcapChangelog = { escapeHtml, inline, parseChangelog };
})(typeof window !== "undefined" ? window : globalThis);
