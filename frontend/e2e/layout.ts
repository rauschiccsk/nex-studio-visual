/**
 * DEV-62 — what "the page fits the screen" means, measured in the browser.
 *
 * Three rules, each over everything on the page — no list of "main actions" to keep up to date:
 *
 * 1. The page never scrolls sideways.
 * 2. No element sticks out of the screen or is cut off by a box that hides its overflow. The one exception is a
 *    frame that scrolls sideways on purpose — a table's frame, a code block, or a box marked `data-scroll-x`, with
 *    `overflow-x: auto` or `scroll` — whose content may be wider; the frame itself must fit. A frame that only
 *    hides its overflow is no exception: what it hides, a finger cannot reach.
 * 3. No text is squeezed: a sentence of four words or more that wraps almost word by word (1.5 words a line or
 *    fewer) stands in a column too narrow to read — nothing sticks out, and yet nobody can use it.
 * 4. Every control (button, link, field, tab) can be reached: scrolled into view, the point in its middle hits the
 *    control itself, not something lying over it. On a touch screen it is at least {@link TOUCH_PX} px in both
 *    directions — except a link inside running text, which is as tall as its line. While a modal dialog is open,
 *    the controls inside it.
 * 5. A figure with its unit („280,6 kT“, „561,20 €“, „100 %“) is never split over two lines. DEV-265: a statement line
 *    naming 31 tasks squeezed the number columns of Fakturácia until every amount broke after its digits.
 */

/** What opens something more on a page: a dialog, or content folded away. Each is opened and measured too. */
export const OPENERS = 'main [aria-haspopup="dialog"]:not(:disabled):visible, main [aria-expanded="false"]:not(:disabled):visible';

import type { Page } from "@playwright/test";

/** The smallest control a finger hits reliably (Apple HIG, WCAG 2.5.5). */
export const TOUCH_PX = 44;

export interface Problem {
  rule: "page-scrolls-sideways" | "sticks-out" | "squeezed" | "covered" | "too-small-to-touch" | "number-split";
  what: string;
  detail: string;
}

/** Runs inside the page: returns the problems of what is on the screen right now. */
function measure(touchPx: number): Problem[] {
  const vw = window.innerWidth;
  /** Sub-pixel rounding of the layout, nothing more: a whole pixel past the edge is already a cut-off letter. */
  const EDGE = 0.5;
  const problems: Problem[] = [];
  const doc = document.documentElement;

  const describe = (el: Element): string => {
    const tag = el.tagName.toLowerCase();
    const label =
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      (el as HTMLElement).innerText?.trim().split("\n")[0] ||
      el.getAttribute("placeholder") ||
      "";
    const cls = typeof el.className === "string" ? el.className.split(/\s+/).slice(0, 4).join(".") : "";
    return `${tag}${cls ? "." + cls : ""}${label ? ` „${label.slice(0, 40)}“` : ""}`;
  };
  const shown = (el: Element): boolean => {
    for (let n: Element | null = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return false;
      if (n.hasAttribute("inert") || n.getAttribute("aria-hidden") === "true") return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // A frame that hides its overflow cuts its content off; only one that scrolls lets a finger reach it.
  const scrolls = (el: Element): boolean => ["auto", "scroll"].includes(getComputedStyle(el).overflowX);
  const scrollsSidewaysOnPurpose = (el: Element): boolean =>
    scrolls(el) &&
    (el.hasAttribute("data-scroll-x") ||
      ["PRE", "CODE", "TEXTAREA"].includes(el.tagName) ||
      el.querySelector(":scope > table, :scope > * > table") !== null ||
      el.classList.contains("xterm-viewport"));
  const clips = (el: Element): boolean => getComputedStyle(el).overflowX !== "visible";
  // DEV-86: the editor of VS Code (NS Code) lays out its own inside — a canvas of lines millions of pixels wide that it
  // scrolls with its own scrollbars, and beside it a text for screen readers placed off the screen
  // (`.monaco-aria-container`). Its box is measured like any other; what lies inside it is the widget's own business,
  // as the terminal's viewport above. Its controls are still checked by rule 4.
  const insideOwnWidget = (node: Node): boolean =>
    (node instanceof Element && node.matches(".monaco-aria-container")) ||
    node.parentElement?.closest(".monaco-editor, .monaco-aria-container") != null;

  if (doc.scrollWidth > vw) {
    problems.push({ rule: "page-scrolls-sideways", what: "page", detail: `${doc.scrollWidth - vw} px wider than the screen` });
  }

  // Rule 2 — outermost offenders only: a child of a reported element is the same problem.
  const offenders = new Set<Element>();
  /** The horizontal room the box `from` (and its ancestors) leaves for what is inside it; `null` = inside a frame
   *  that scrolls on purpose, or inside something already reported. */
  const roomIn = (from: Element | null) => {
    let left = 0;
    let right = vw;
    let clippedBy: Element | null = null;
    for (let a = from; a && a !== document.body; a = a.parentElement) {
      if (offenders.has(a)) return null;
      if (clips(a)) {
        if (scrollsSidewaysOnPurpose(a)) return null;
        const r = a.getBoundingClientRect();
        if (r.left > left) left = r.left;
        if (r.right < right) {
          right = r.right;
          clippedBy = a;
        }
      }
      // A fixed box (a dialog, the drawer) is placed against the screen: the boxes around it do not cut it off.
      if (getComputedStyle(a).position === "fixed") break;
    }
    return { left, right, clippedBy };
  };
  const report = (what: string, from: number, to: number, room: { left: number; right: number; clippedBy: Element | null }) => {
    const where = room.clippedBy ? `cut off by ${describe(room.clippedBy)}` : "outside the screen";
    problems.push({
      rule: "sticks-out",
      what,
      detail: `${where}: ${Math.round(from)}–${Math.round(to)} px, room ${Math.round(room.left)}–${Math.round(room.right)} px`,
    });
  };
  for (const el of Array.from(document.body.querySelectorAll("*"))) {
    if (!shown(el) || insideOwnWidget(el)) continue;
    const room =
      getComputedStyle(el).position === "fixed" ? { left: 0, right: vw, clippedBy: null } : roomIn(el.parentElement);
    if (!room) continue;
    const r = el.getBoundingClientRect();
    if (r.right > room.right + EDGE || r.left < room.left - EDGE) {
      offenders.add(el);
      report(describe(el), r.left, r.right, room);
    }
  }
  // Text that runs out of its own box (a word too long for the line) is not a box of its own — measured line by
  // line. A box that cuts its text off with an ellipsis („…“) does so on purpose.
  const texts = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = texts.nextNode(); node; node = texts.nextNode()) {
    const parent = node.parentElement;
    if (!parent || !node.textContent?.trim() || !shown(parent) || insideOwnWidget(node)) continue;
    if (Array.from(offenders).some((o) => o.contains(parent))) continue;
    const room = roomIn(parent);
    if (!room || (room.clippedBy && getComputedStyle(room.clippedBy).textOverflow === "ellipsis")) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    for (const line of Array.from(range.getClientRects())) {
      if (line.width > 0 && (line.right > room.right + EDGE || line.left < room.left - EDGE)) {
        offenders.add(parent);
        report(`text „${node.textContent.trim().slice(0, 40)}“ in ${describe(parent)}`, line.left, line.right, room);
        break;
      }
    }
  }

  // Rule 5 — a figure with its unit on one line.
  const figure = /^[−–—-]?\s*\d[\d\s\u00a0\u202f.,]*\s*(kT|€|%)(\s*\/\s*kT)?$/;
  const figures = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = figures.nextNode(); node; node = figures.nextNode()) {
    const parent = node.parentElement;
    const text = (node.textContent ?? "").trim();
    if (!parent || !figure.test(text) || !shown(parent) || insideOwnWidget(node)) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const lines = new Set(Array.from(range.getClientRects()).filter((r) => r.width > 0).map((r) => Math.round(r.top))).size;
    if (lines > 1) {
      problems.push({ rule: "number-split", what: `„${text}“ in ${describe(parent)}`, detail: `on ${lines} lines` });
    }
  }

  // Rule 3.
  const squeezed = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = squeezed.nextNode(); node; node = squeezed.nextNode()) {
    const parent = node.parentElement;
    const words = (node.textContent ?? "").trim().split(/\s+/).filter(Boolean).length;
    if (!parent || words < 4 || !shown(parent) || insideOwnWidget(node)) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const lines = new Set(Array.from(range.getClientRects()).filter((r) => r.width > 0).map((r) => Math.round(r.top))).size;
    if (lines >= 3 && words / lines <= 1.5) {
      problems.push({
        rule: "squeezed",
        what: `text „${(node.textContent ?? "").trim().slice(0, 40)}“ in ${describe(parent)}`,
        detail: `${words} words on ${lines} lines, ${Math.round(parent.getBoundingClientRect().width)} px wide`,
      });
    }
  }

  // Rule 4. While a modal dialog is open, only its own controls can be reached — the rest lies under its backdrop.
  const modal = Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).filter(shown).pop();
  const controls = Array.from(
    document.body.querySelectorAll(
      'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"], [role="link"], [role="switch"], [role="checkbox"]',
    ),
  ).filter((el) => shown(el) && (!modal || modal.contains(el)));
  for (const el of controls) {
    if (Array.from(offenders).some((o) => o.contains(el))) continue; // reported as sticking out
    const html = el as HTMLElement;
    html.scrollIntoView({ block: "center", inline: "center" });
    const r = el.getBoundingClientRect();
    const disabled = html.matches(":disabled") || el.getAttribute("aria-disabled") === "true";
    if (!disabled) {
      const x = Math.min(Math.max(r.left + r.width / 2, 0), vw - 1);
      const y = Math.min(Math.max(r.top + r.height / 2, 0), window.innerHeight - 1);
      const hit = document.elementFromPoint(x, y);
      const label = el.closest("label");
      if (hit && !el.contains(hit) && !(label && label.contains(hit)) && !hit.contains(el)) {
        problems.push({ rule: "covered", what: describe(el), detail: `the middle hits ${describe(hit)}` });
      }
    }
    if (touchPx > 0) {
      const inText = getComputedStyle(el).display === "inline" && el.tagName === "A";
      const small = r.height < touchPx - 0.5 || r.width < touchPx - 0.5;
      // A checkbox or radio is touched through its label: the label is what must be large enough.
      const target = (el.matches('input[type="checkbox"], input[type="radio"]') && el.closest("label")) || el;
      const t = target.getBoundingClientRect();
      if (!inText && small && (t.height < touchPx - 0.5 || t.width < touchPx - 0.5)) {
        problems.push({ rule: "too-small-to-touch", what: describe(el), detail: `${Math.round(t.width)}×${Math.round(t.height)} px` });
      }
    }
  }
  window.scrollTo(0, 0);
  return problems;
}

export async function problemsOn(page: Page, touch: boolean): Promise<Problem[]> {
  return page.evaluate(measure, touch ? TOUCH_PX : 0);
}

/** Let the page settle: its API calls answered, fonts loaded, the first effects run. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
}
