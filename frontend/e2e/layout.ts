/**
 * DEV-62 — what "the page fits the screen" means, measured in the browser.
 *
 * Three rules, each over everything on the page — no list of "main actions" to keep up to date:
 *
 * 1. The page never scrolls sideways.
 * 2. No element sticks out of the screen or is cut off by a box that hides its overflow. The one exception is a
 *    frame meant to scroll sideways — a table's frame, a code block, or a box marked `data-scroll-x` — whose
 *    content may be wider; the frame itself must fit.
 * 3. Every control (button, link, field, tab) can be reached: scrolled into view, the point in its middle hits the
 *    control itself, not something lying over it. On a touch screen it is at least {@link TOUCH_PX} px in both
 *    directions — except a link inside running text, which is as tall as its line.
 */

import type { Page } from "@playwright/test";

/** The smallest control a finger hits reliably (Apple HIG, WCAG 2.5.5). */
export const TOUCH_PX = 44;

export interface Problem {
  rule: "page-scrolls-sideways" | "sticks-out" | "covered" | "too-small-to-touch";
  what: string;
  detail: string;
}

/** Runs inside the page: returns the problems of what is on the screen right now. */
function measure(touchPx: number): Problem[] {
  const vw = window.innerWidth;
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
  const scrollsSidewaysOnPurpose = (el: Element): boolean =>
    el.hasAttribute("data-scroll-x") ||
    ["PRE", "CODE", "TEXTAREA"].includes(el.tagName) ||
    el.querySelector(":scope > table, :scope > * > table") !== null ||
    el.classList.contains("xterm-viewport");
  const clips = (el: Element): boolean => getComputedStyle(el).overflowX !== "visible";

  if (doc.scrollWidth > vw + 1) {
    problems.push({ rule: "page-scrolls-sideways", what: "page", detail: `${doc.scrollWidth - vw} px wider than the screen` });
  }

  // Rule 2 — outermost offenders only: a child of a reported element is the same problem.
  const offenders = new Set<Element>();
  for (const el of Array.from(document.body.querySelectorAll("*"))) {
    if (!shown(el)) continue;
    let left = 0;
    let right = vw;
    let clippedBy: Element | null = null;
    let insideFrame = false;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      if (offenders.has(a)) {
        insideFrame = true; // already reported through its ancestor
        break;
      }
      if (clips(a) && scrollsSidewaysOnPurpose(a)) {
        insideFrame = true;
        break;
      }
      if (clips(a)) {
        const r = a.getBoundingClientRect();
        if (r.left > left) left = r.left;
        if (r.right < right) {
          right = r.right;
          clippedBy = a;
        }
      }
    }
    if (insideFrame) continue;
    const r = el.getBoundingClientRect();
    if (r.right > right + 1 || r.left < left - 1) {
      offenders.add(el);
      const where = clippedBy ? `cut off by ${describe(clippedBy)}` : "outside the screen";
      problems.push({
        rule: "sticks-out",
        what: describe(el),
        detail: `${where}: ${Math.round(r.left)}–${Math.round(r.right)} px, room ${Math.round(left)}–${Math.round(right)} px`,
      });
    }
  }

  // Rule 3.
  const controls = Array.from(
    document.body.querySelectorAll(
      'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"], [role="link"], [role="switch"], [role="checkbox"]',
    ),
  ).filter(shown);
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
