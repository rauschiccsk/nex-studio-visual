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
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

/** Fallback when the computed line-height is ``normal`` (no numeric value to read). */
const FALLBACK_LINE_HEIGHT_PX = 20;

export const DEFAULT_MAX_ROWS = 20;

export function useAutoGrowTextarea(
  value: string,
  maxRows: number = DEFAULT_MAX_ROWS,
): RefObject<HTMLTextAreaElement | null> {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // Layout effect, not a plain effect: the height is written before the browser paints, so the box
  // never shows at the wrong size for a frame.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const styles = window.getComputedStyle(el);
    const parsed = Number.parseFloat(styles.lineHeight);
    const lineHeight = Number.isFinite(parsed) ? parsed : FALLBACK_LINE_HEIGHT_PX;
    const padding = Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom);
    const border =
      Number.parseFloat(styles.borderTopWidth) + Number.parseFloat(styles.borderBottomWidth);
    const maxHeight = lineHeight * maxRows + (Number.isFinite(padding) ? padding : 0) +
      (Number.isFinite(border) ? border : 0);

    // Collapse first: ``scrollHeight`` reports the CURRENT height when the content is shorter than
    // the box, so without this the box would only ever grow and never shrink back.
    el.style.height = "auto";
    const wanted = el.scrollHeight;
    el.style.height = `${Math.min(wanted, maxHeight)}px`;
    // Past the cap the content scrolls inside the box instead of pushing it taller.
    el.style.overflowY = wanted > maxHeight ? "auto" : "hidden";
  }, [value, maxRows]);

  // A window resize changes the wrap points, so the same text may need a different number of rows.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onResize = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return ref;
}
