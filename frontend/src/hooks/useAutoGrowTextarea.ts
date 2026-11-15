/**
 * useAutoGrowTextarea — a message box that grows with what you type, then scrolls.
 *
 * Both places the Manažér writes to the AI Agent used to be fixed-height: the recovery bar was a
 * single-line ``<input>`` (a five-sentence answer scrolled sideways through a 1.5rem slot, and the
 * text already typed was invisible), and the composer was pinned at two rows. Neither showed the
 * message being written, which is the one thing a person needs to see before pressing send.
 *
 * The box grows to ``maxRows`` and only then scrolls — an upper bound rather than unbounded growth,
 * because a box that eats the whole screen hides the conversation it belongs to.
 *
 * Height is driven by ``value`` rather than by keystrokes so it is correct after a programmatic
 * change too — clearing the box on a successful send must shrink it back, and that assignment never
 * fires a key event.
 *
 * DEV-390 — and by the box itself: 15.11.2026 the answer box of Riadiace centrum came back from Poradca on two rows.
 * Its text is restored at once, but the box appears only when the build's state has loaded — with the text unchanged,
 * so a height driven by ``value`` alone was never measured. The height is measured whenever the box appears, its text
 * changes, or its width changes (a narrower panel wraps the same text onto more rows).
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

/** Fallback when the computed line-height is ``normal`` (no numeric value to read). */
const FALLBACK_LINE_HEIGHT_PX = 20;

const DEFAULT_MAX_ROWS = 20;

/** Size ``el`` to its text, up to ``maxRows``; past that the text scrolls inside the box. */
function fit(el: HTMLTextAreaElement, maxRows: number): void {
  const styles = window.getComputedStyle(el);
  const parsed = Number.parseFloat(styles.lineHeight);
  const lineHeight = Number.isFinite(parsed) ? parsed : FALLBACK_LINE_HEIGHT_PX;
  const padding = Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom);
  const border = Number.parseFloat(styles.borderTopWidth) + Number.parseFloat(styles.borderBottomWidth);
  const maxHeight =
    lineHeight * maxRows + (Number.isFinite(padding) ? padding : 0) + (Number.isFinite(border) ? border : 0);

  // Collapse first: ``scrollHeight`` reports the CURRENT height when the content is shorter than
  // the box, so without this the box would only ever grow and never shrink back.
  el.style.height = "auto";
  const wanted = el.scrollHeight;
  el.style.height = `${Math.min(wanted, maxHeight)}px`;
  // Past the cap the content scrolls inside the box instead of pushing it taller.
  el.style.overflowY = wanted > maxHeight ? "auto" : "hidden";
}

export function useAutoGrowTextarea(
  value: string,
  maxRows: number = DEFAULT_MAX_ROWS,
): RefObject<HTMLTextAreaElement | null> {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  /** What the box was last sized for — the box itself, its text and its cap. */
  const sized = useRef<{ el: HTMLTextAreaElement | null; value: string; maxRows: number }>({
    el: null,
    value: "",
    maxRows,
  });
  /** Who watches the box's width, and which box. */
  const watch = useRef<{ el: HTMLTextAreaElement; stop: () => void } | null>(null);

  // After every render, not only when ``value`` changes: the box may appear in a later render with the same text
  // (DEV-390). Layout effect, not a plain effect: the height is written before the browser paints, so the box never
  // shows at the wrong size for a frame. Sized only when something it depends on changed.
  useLayoutEffect(() => {
    const el = ref.current;
    if (watch.current && watch.current.el !== el) {
      watch.current.stop();
      watch.current = null;
    }
    if (el && !watch.current) watch.current = { el, stop: watchWidth(el, () => sized.current.maxRows) };
    const last = sized.current;
    if (!el || (el === last.el && value === last.value && maxRows === last.maxRows)) return;
    sized.current = { el, value, maxRows };
    fit(el, maxRows);
  });

  useEffect(
    () => () => {
      watch.current?.stop();
      watch.current = null;
    },
    [],
  );

  return ref;
}

/** A change of the box's width changes the wrap points, so the same text may need a different number of rows — a
 *  window resize, or a panel beside it that appears. Only the width: the height is what ``fit`` itself writes. */
function watchWidth(el: HTMLTextAreaElement, maxRows: () => number): () => void {
  let width = el.clientWidth;
  const refit = () => {
    if (el.clientWidth === width) return;
    width = el.clientWidth;
    fit(el, maxRows());
  };
  if (typeof ResizeObserver === "undefined") {
    window.addEventListener("resize", refit);
    return () => window.removeEventListener("resize", refit);
  }
  const observer = new ResizeObserver(refit);
  observer.observe(el);
  return () => observer.disconnect();
}
