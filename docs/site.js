// SPDX-FileCopyrightText: 2026 Nave Kuhar
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The website's shared script: light and dark styles, the menu on phones, view tabs,
// full-size screenshots, copy buttons, the quick add demo and the guide's contents list.

(() => {
// The repository's address, like "https://github.com/you/navcal". While it's empty, the
// links to the source code, the download button and the clone step stay hidden.
const REPO = "";

const root = document.documentElement;
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
const icon = (name, cls = "icon") =>
  `<svg class="${cls}" aria-hidden="true"><use href="#${name}"/></svg>`;

// -- repository links -------------------------------------------------------------------
{
  const repo = REPO.trim().replace(/\/+$/, "").replace(/\.git$/, "");
  if (repo) {
    for (const el of document.querySelectorAll("[data-repo-only]")) el.hidden = false;
    for (const a of document.querySelectorAll("[data-repo-link]")) a.href = repo + (a.dataset.repoPath || "");
    for (const el of document.querySelectorAll("[data-repo-url]")) el.textContent = repo;
  }
}

// -- light and dark ---------------------------------------------------------------------
// The choice ("system", "light" or "dark") is kept in this browser only.
function storedTheme() {
  try {
    const t = localStorage.getItem("theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function applyTheme(choice) {
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  const shown = choice === "system" ? (darkQuery.matches ? "dark" : "light") : choice;
  root.dataset.shownTheme = shown;
  // Screenshots: <source media="(prefers-color-scheme: dark)"> only follows the system,
  // so rewrite that part of the query to follow the choice.
  const forced = {system: null, dark: "(min-width: 0px)", light: "(max-width: 0px)"}[choice];
  for (const source of document.querySelectorAll("source[data-media], source[media*='prefers-color-scheme']")) {
    source.dataset.media ??= source.media;
    source.media = forced ? source.dataset.media.replace("(prefers-color-scheme: dark)", forced) : source.dataset.media;
  }
  for (const meta of document.querySelectorAll("meta[name='theme-color']")) {
    meta.dataset.media ??= meta.media;
    meta.media = forced ? "all" : meta.dataset.media;
    if (forced) meta.content = shown === "dark" ? "#222226" : "#fafafb";
    else meta.content = meta.dataset.media.includes("dark") ? "#222226" : "#fafafb";
  }
  const toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    const label = shown === "dark" ? "Use light style" : "Use dark style";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  }
  for (const input of document.querySelectorAll("input[name='theme']")) input.checked = input.value === choice;
  refreshLightbox();
}

function chooseTheme(choice) {
  try {
    if (choice === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", choice);
  } catch { /* not kept: fine */ }
  applyTheme(choice);
}

document.querySelector(".theme-toggle")?.addEventListener("click", () => {
  const shown = root.dataset.shownTheme;
  const next = shown === "dark" ? "light" : "dark";
  // Back to following the system when that's what it would show anyway.
  chooseTheme((darkQuery.matches ? "dark" : "light") === next ? "system" : next);
});
for (const input of document.querySelectorAll("input[name='theme']")) {
  input.addEventListener("change", () => chooseTheme(input.value));
}
darkQuery.addEventListener("change", () => applyTheme(storedTheme()));

// -- menu on narrow screens -------------------------------------------------------------
{
  const nav = document.querySelector(".nav");
  const toggle = nav?.querySelector(".menu-toggle");
  if (toggle) {
    toggle.hidden = false;
    const setOpen = open => {
      nav.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.innerHTML = icon(open ? "close" : "menu");
      toggle.setAttribute("aria-label", open ? "Close menu" : "Menu");
    };
    toggle.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
    nav.querySelector(".nav-links").addEventListener("click", e => { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && nav.classList.contains("open")) { setOpen(false); toggle.focus(); }
    });
    document.addEventListener("click", e => {
      if (nav.classList.contains("open") && !nav.contains(e.target)) setOpen(false);
    });
  }
}

// -- tabs -------------------------------------------------------------------------------
for (const tabs of document.querySelectorAll("[data-tabs]")) {
  const buttons = [...tabs.querySelectorAll("[role=tab]")];
  const select = (button, focus) => {
    for (const b of buttons) {
      const on = b === button;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      document.getElementById(b.getAttribute("aria-controls")).classList.toggle("active", on);
    }
    if (focus) button.focus();
  };
  for (const b of buttons) {
    b.addEventListener("click", () => select(b, false));
    b.addEventListener("keydown", e => {
      const i = buttons.indexOf(b);
      const to = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: buttons.length - 1}[e.key];
      if (to === undefined) return;
      e.preventDefault();
      select(buttons[(to + buttons.length) % buttons.length], true);
    });
  }
}

// -- full-size screenshots --------------------------------------------------------------
let lightbox = null;
let lightboxSource = null;

function openLightbox(img) {
  if (!lightbox) {
    lightbox = document.createElement("dialog");
    lightbox.className = "lightbox";
    lightbox.setAttribute("aria-label", "Screenshot");
    lightbox.innerHTML = `<div class="lightbox-bar"><button class="icon-button" type="button" aria-label="Close" title="Close">${icon("close")}</button></div>
      <figure><img alt=""></figure><p></p>`;
    document.body.append(lightbox);
    lightbox.querySelector("button").addEventListener("click", () => lightbox.close());
    // A click anywhere but on the picture closes it.
    lightbox.addEventListener("click", e => { if (e.target.tagName !== "IMG" && !e.target.closest("button")) lightbox.close(); });
    lightbox.addEventListener("close", () => { lightboxSource?.focus?.(); lightboxSource = null; });
  }
  lightboxSource = img;
  showInLightbox();
  lightbox.querySelector("p").textContent = img.alt;
  lightbox.showModal();
  lightbox.querySelector("button").focus();
}

function showInLightbox() {
  if (!lightboxSource) return;
  const big = lightbox.querySelector("img");
  big.src = lightboxSource.currentSrc || lightboxSource.src;
  big.alt = lightboxSource.alt;
}

function refreshLightbox() {
  if (!lightbox?.open || !lightboxSource) return;
  // The page picks the light or dark picture a moment after the style changes.
  lightboxSource.addEventListener("load", showInLightbox, {once: true});
}

for (const img of document.querySelectorAll(".shot img, .doc-body figure img")) {
  img.tabIndex = 0;
  img.setAttribute("role", "button");
  img.setAttribute("aria-haspopup", "dialog");
  img.addEventListener("click", () => openLightbox(img));
  img.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openLightbox(img); }
  });
}

// -- copy buttons -----------------------------------------------------------------------
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = Object.assign(document.createElement("textarea"), {value: text});
    area.style.cssText = "position:fixed;opacity:0";
    document.body.append(area);
    area.select();
    const done = document.execCommand("copy");
    area.remove();
    return done;
  }
}

for (const pre of document.querySelectorAll("pre:not([data-no-copy])")) {
  const box = document.createElement("div");
  box.className = "code";
  pre.replaceWith(box);
  box.append(pre);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  button.innerHTML = icon("copy");
  button.setAttribute("aria-label", "Copy");
  button.title = "Copy";
  box.append(button);
  button.addEventListener("click", async () => {
    // Only the commands: comments are for reading.
    const text = pre.innerText.split("\n").filter(line => !line.trim().startsWith("#")).join("\n").trim();
    if (!(await copyText(text))) return;
    button.innerHTML = icon("check");
    button.classList.add("done");
    button.setAttribute("aria-label", "Copied");
    button.title = "Copied";
    clearTimeout(button.timer);
    button.timer = setTimeout(() => {
      button.innerHTML = icon("copy");
      button.classList.remove("done");
      button.setAttribute("aria-label", "Copy");
      button.title = "Copy";
    }, 1600);
  });
}

// -- appearing on scroll ----------------------------------------------------------------
{
  const items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const seen = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) { entry.target.classList.add("shown"); seen.unobserve(entry.target); }
      }
    }, {rootMargin: "0px 0px -8% 0px"});
    for (const el of items) seen.observe(el);
  } else {
    for (const el of items) el.classList.add("shown");
  }
}

// -- quick add demo ---------------------------------------------------------------------
function startDemo(demo) {
  const {parse} = NavcalQuickAdd;
  const input = demo.querySelector("input");
  const add = demo.querySelector("[data-add]");
  const preview = demo.querySelector(".qa-preview");
  const added = demo.querySelector(".added");
  const colors = ["--blue", "--green", "--purple", "--orange", "--teal", "--red"];
  let count = 0;

  // Dates in English, like the rest of the page, with the visitor's own 12- or 24-hour clock.
  const hourCycle = new Intl.DateTimeFormat(undefined, {hour: "numeric"}).resolvedOptions().hourCycle;
  const day = d => d.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
  const time = d => d.toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit", hourCycle});
  const sameDay = (a, b) => a.toDateString() === b.toDateString();

  function describeWhen(ev) {
    if (ev.allDay) {
      const last = new Date(ev.end.getFullYear(), ev.end.getMonth(), ev.end.getDate() - 1);
      return sameDay(ev.start, last) || last < ev.start ? `${day(ev.start)}, all day` : `${day(ev.start)} – ${day(last)}`;
    }
    const midnight = new Date(ev.start.getFullYear(), ev.start.getMonth(), ev.start.getDate() + 1);
    if (sameDay(ev.start, ev.end) || +ev.end === +midnight) return `${day(ev.start)}, ${time(ev.start)} – ${time(ev.end)}`;
    const endDay = ev.end.toLocaleDateString("en-US", {weekday: "short"});
    return `${day(ev.start)}, ${time(ev.start)} – ${time(ev.end)} (${endDay})`;
  }

  function describeRepeat(ev) {
    const n = ev.interval;
    if (ev.freq === "weekly" && n === 1 && [...ev.byday].sort().join() === "0,1,2,3,4") return "Repeats every weekday";
    const unit = {daily: "day", weekly: "week", monthly: "month", yearly: "year"}[ev.freq];
    let text = n === 1 ? `Repeats every ${unit}` : `Repeats every ${n} ${unit}s`;
    if (ev.freq === "weekly" && ev.byday.length) {
      const names = [...ev.byday].sort().map(d => new Date(2026, 8, 21 + d).toLocaleDateString("en-US", {weekday: "short"}));
      text += ` on ${names.join(", ")}`;
    }
    return text;
  }

  function row(iconName, text, cls = "") {
    const p = document.createElement("p");
    if (cls) p.className = cls;
    p.innerHTML = iconName ? icon(iconName) : "";
    p.append(Object.assign(document.createElement("span"), {textContent: text}));
    return p;
  }

  let current = null;
  function update() {
    current = parse(input.value, new Date());
    preview.replaceChildren();
    add.disabled = !current.title;
    if (!current.title) {
      preview.append(row("", "Type what, when and where", "qa-hint"));
      return;
    }
    preview.append(row("", current.title, "qa-title"), row("clock", describeWhen(current)));
    if (current.location) preview.append(row("pin", current.location));
    if (current.freq !== "none") preview.append(row("repeat", describeRepeat(current)));
  }

  function addEvent() {
    if (!current?.title) return;
    const li = document.createElement("li");
    li.style.setProperty("--dot", `var(${colors[count++ % colors.length]})`);
    const details = [describeWhen(current), current.location, current.freq !== "none" ? describeRepeat(current) : ""];
    li.append(Object.assign(document.createElement("b"), {textContent: current.title}),
              Object.assign(document.createElement("span"), {textContent: details.filter(Boolean).join(" · ")}));
    added.prepend(li);
    while (added.children.length > 3) added.lastElementChild.remove();
    added.hidden = false;
    input.value = "";
    update();
    input.focus();
  }

  input.addEventListener("input", update);
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addEvent(); } });
  add.addEventListener("click", addEvent);
  for (const chip of demo.querySelectorAll(".chips button")) {
    chip.addEventListener("click", () => { input.value = chip.textContent; update(); input.focus(); });
  }
  update();
}
const demo = document.querySelector("[data-quickadd]");
if (demo) startDemo(demo);

// -- the guide: headings to link to, contents that follow along, shortcut filter --------
{
  const body = document.querySelector(".doc-body");
  if (body) {
    for (const h of body.querySelectorAll("section[id] > h2, h3[id]")) {
      const id = h.id || h.parentElement.id;
      const a = Object.assign(document.createElement("a"), {className: "anchor", href: `#${id}`, textContent: "#"});
      a.setAttribute("aria-label", `Link to “${h.textContent}”`);
      h.append(a);
    }
    const toc = document.querySelector(".toc");
    const narrow = matchMedia("(max-width: 900px)");
    const fitToc = () => { toc.open = !narrow.matches; };
    fitToc();
    narrow.addEventListener("change", fitToc);
    toc.addEventListener("click", e => { if (e.target.closest("a") && narrow.matches) toc.open = false; });

    const links = new Map([...toc.querySelectorAll("a")].map(a => [a.hash.slice(1), a]));
    const sections = [...body.querySelectorAll(":scope > section[id]")];
    const mark = () => {
      // The last section whose top has passed a line under the top bar.
      const line = 120;
      let current = sections[0];
      for (const s of sections) if (s.getBoundingClientRect().top <= line) current = s;
      if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4) current = sections.at(-1);
      for (const [id, a] of links) {
        a.classList.toggle("current", id === current.id);
        if (id === current.id) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
      }
    };
    let queued = false;
    addEventListener("scroll", () => {
      if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; mark(); }); }
    }, {passive: true});
    mark();
  }

  const filter = document.querySelector("[data-filter]");
  if (filter) {
    const scope = document.getElementById(filter.dataset.filter);
    const empty = scope.querySelector(".no-match");
    filter.addEventListener("input", () => {
      const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
      let any = false;
      for (const group of scope.querySelectorAll(".shortcut-group")) {
        let shown = 0;
        for (const tr of group.querySelectorAll("tbody tr")) {
          const text = tr.textContent.toLowerCase();
          const match = words.every(w => text.includes(w));
          tr.hidden = !match;
          shown += match;
        }
        group.hidden = !shown;
        any ||= shown > 0;
      }
      empty.hidden = any;
    });
  }
}

applyTheme(storedTheme());
})();
