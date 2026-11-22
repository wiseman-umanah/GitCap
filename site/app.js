// gitcap site: theme, install tabs, copy, terminal replay, and the changelog read from CHANGELOG.md.
(() => {
  "use strict";

  const REPO = "wiseman-umanah/GitCap";
  // The Pages deploy copies CHANGELOG.md next to this page; GitHub's raw file covers local previews.
  const CHANGELOG_SOURCES = ["CHANGELOG.md", `https://raw.githubusercontent.com/${REPO}/master/CHANGELOG.md`];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
  };

  // ------------------------------------------------------------------ theme

  const root = document.documentElement;
  const savedTheme = store.get("gitcap-theme");
  if (savedTheme === "light" || savedTheme === "dark") root.dataset.theme = savedTheme;

  document.querySelector(".theme")?.addEventListener("click", () => {
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const current = root.dataset.theme || (systemDark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    store.set("gitcap-theme", next);
  });

  // ------------------------------------------------------------------ install tabs + copy

  document.querySelectorAll("[data-tabs]").forEach((tabs) => {
    const target = tabs.querySelector("[data-install]");
    tabs.querySelectorAll("[role=tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.querySelectorAll("[role=tab]").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
        target.textContent = tab.dataset.cmd;
      });
    });
  });

  document.querySelectorAll("[data-copy-from]").forEach((button) => {
    button.addEventListener("click", async () => {
      const text = document.querySelector(button.dataset.copyFrom)?.textContent ?? "";
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
      } catch {
        button.textContent = "Select it";
      }
      button.dataset.done = "";
      setTimeout(() => { button.textContent = "Copy"; delete button.dataset.done; }, 1600);
    });
  });

  // ------------------------------------------------------------------ terminal replay

  // A real gitcap session, in gitcap's real output format: the first agent is out of credits,
  // the next one takes over.
  // Draws gitcap's rich panel at an exact width, so the right edge lines up.
  function panel(title, rows, width) {
    const inner = width - 2;
    const top = `╭─ ${title} ${"─".repeat(inner - title.length - 3)}╮`;
    const line = (text, cls) => {
      const body = cls ? `<span class="${cls}">${text}</span>` : text;
      return `<span class="t-box">│</span>${body}${" ".repeat(Math.max(inner - [...text].length, 0))}<span class="t-box">│</span>`;
    };
    return '<span class="t-panel">' + [
      `<span class="t-box">${top}</span>`,
      line(""),
      ...rows.map(([text, cls]) => line(text ? `  ${text}` : "", cls)),
      line(""),
      `<span class="t-box">╰${"─".repeat(inner)}╯</span>`,
    ].join("\n") + "</span>";
  }

  // A real gitcap session, in gitcap's real output format: the first agent is out of credits,
  // the next one takes over.
  const session = (width) => [
    { type: "cmd", text: "git add calc.py" },
    { type: "cmd", text: "gitcap commit" },
    { type: "out", wait: 900, html: '<span class="t-cyan">⠹</span> <span class="t-cyan">Qoder CLI</span> is writing your commit message…' },
    { type: "out", wait: 1500, html: '<span class="t-dim">↷ Qoder CLI failed: You\'ve reached your credit usage limit. (skipping it for 6h) — trying next</span>', replaceLast: true },
    { type: "out", wait: 300, html: '<span class="t-cyan">⠼</span> <span class="t-cyan">Claude Code</span> is writing your commit message…' },
    {
      type: "out", wait: 1700, replaceLast: true, html: [
        panel("commit message", [
          ["feat(calc): add sub()", "t-bold"],
          [""],
          ["- add sub(a, b)"],
          ["- document add()"],
        ], width),
        '  <span class="t-cyan">claude</span> · claude-haiku-5-5 · 4.1s   <span class="t-bold">1.5k</span> in · <span class="t-bold">40</span> out · $0.0003',
      ].join("\n"),
    },
    { type: "out", wait: 500, html: '<span class="t-cyan t-bold">c</span> commit  <span class="t-cyan t-bold">e</span> edit  <span class="t-cyan t-bold">r</span> regenerate  <span class="t-cyan t-bold">q</span> quit' },
    { type: "out", wait: 1300, html: '<span class="t-green">✓</span> [main 4f2a91c] feat(calc): add sub()\n 1 file changed, 6 insertions(+)' },
    { type: "cmd", text: "", final: true },
  ];

  const term = document.querySelector("[data-term]");
  let run = 0;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const prompt = '<span class="t-acc">$</span> ';
  const caret = '<span class="t-caret"></span>';

  // How many monospace characters fit across the terminal, so the panel never wraps.
  function columns() {
    const probe = document.createElement("span");
    probe.textContent = "0".repeat(20);
    probe.style.visibility = "hidden";
    term.appendChild(probe);
    const charWidth = probe.getBoundingClientRect().width / 20 || 8;
    probe.remove();
    const style = getComputedStyle(term);
    const usable = term.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return Math.floor(usable / charWidth);
  }

  async function play() {
    if (!term) return;
    const id = ++run;
    // Measure with the real font: before it loads, the fallback font gives the wrong width.
    if (document.fonts?.ready) await document.fonts.ready.catch(() => {});
    const SESSION = session(Math.max(Math.min(columns() - 3, 50), 30));
    const lines = [];
    const render = (extra = "") => { term.innerHTML = lines.join("\n") + extra; };

    if (reducedMotion) {
      // Same story, no animation: show the end state.
      for (const step of SESSION) {
        if (step.type === "cmd") { if (!step.final) lines.push(prompt + step.text); continue; }
        if (step.replaceLast) lines.pop();
        lines.push(step.html);
      }
      render("\n" + prompt + caret);
      return;
    }

    render(prompt + caret);
    for (const step of SESSION) {
      if (id !== run) return; // replay pressed: a newer run took over
      if (step.type === "cmd") {
        if (step.final) { render((lines.length ? "\n" : "") + prompt + caret); return; }
        await sleep(450);
        let typed = "";
        for (const ch of step.text) {
          if (id !== run) return;
          typed += ch;
          render((lines.length ? "\n" : "") + prompt + typed + caret);
          await sleep(45 + Math.random() * 55);
        }
        await sleep(250);
        lines.push(prompt + step.text);
        render();
        continue;
      }
      await sleep(step.wait || 400);
      if (id !== run) return;
      if (step.replaceLast) lines.pop();
      lines.push(step.html);
      render();
    }
  }

  document.querySelector(".term__replay")?.addEventListener("click", play);
  if (term) {
    // Start when the terminal scrolls into view, not before.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { observer.disconnect(); play(); }
    }, { threshold: 0.35 });
    observer.observe(term);
  }

  // ------------------------------------------------------------------ changelog

  const { escapeHtml, inline, parseChangelog } = window.gitcapChangelog;

  function formatDate(iso) {
    if (!iso) return "";
    const date = new Date(`${iso}T00:00:00Z`);
    return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
  }

  function renderChangelog(container, releases) {
    if (!releases.length) {
      container.innerHTML = '<p class="changelog__status">No releases yet.</p>';
      return;
    }
    container.innerHTML = releases.map((r, i) => {
      const version = escapeHtml(r.version.replace(/^v/, ""));
      const title = r.url && /^https?:\/\//.test(r.url)
        ? `<a href="${escapeHtml(r.url)}" rel="noopener">${version}</a>` : version;
      const sections = r.sections.map((s) => `
        <h4${s.breaking ? ' class="breaking"' : ""}>${escapeHtml(s.title)}</h4>
        <ul>${s.items.map((item) => `<li>${inline(item)}</li>`).join("")}</ul>`).join("");
      return `
        <details class="release"${i === 0 ? " open" : ""}>
          <summary>
            <span class="release__version">${title}</span>
            ${i === 0 ? '<span class="release__tag">latest</span>' : ""}
            <time class="release__date" datetime="${escapeHtml(r.date)}">${escapeHtml(formatDate(r.date))}</time>
          </summary>
          <div class="release__body">${sections || "<p>No notable changes.</p>"}</div>
        </details>`;
    }).join("");
  }

  async function loadChangelog() {
    const container = document.querySelector("[data-changelog]");
    if (!container) return;
    for (const source of CHANGELOG_SOURCES) {
      try {
        const response = await fetch(source, { cache: "no-cache" });
        if (!response.ok) continue;
        const releases = parseChangelog(await response.text());
        renderChangelog(container, releases);
        container.removeAttribute("aria-busy");
        if (releases[0]) {
          document.querySelectorAll("[data-version]").forEach((el) => { el.textContent = `v${releases[0].version.replace(/^v/, "")}`; });
        }
        return;
      } catch { /* try the next source */ }
    }
    container.removeAttribute("aria-busy");
    container.innerHTML = `<p class="changelog__status">Couldn't load the changelog. Read it on <a href="https://github.com/${REPO}/blob/master/CHANGELOG.md">GitHub</a>.</p>`;
  }

  loadChangelog();
})();
