// useFollowBottom — a conversation that keeps the newest text in view while it is being written (DEV-25).
//
// Director 08.10.2026, Poradca answering: „Ak chcem čítať čo píše musím ja skrolovať obrazovku." Riadiace
// centrum follows its thread; Poradca's steps and answer arrived below the bottom edge.
//
// It follows only while the reader is at the end. Someone who scrolled up to re-read an earlier answer is not
// pulled away by every new line; once he is back at the end it follows again. `follow()` is for his own
// question — he sent it, he wants to see what comes back.

import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";

/** Within this many pixels of the end the reader counts as being at the end. */
const AT_END_PX = 40;

export interface FollowBottom<T extends HTMLElement> {
  ref: RefObject<T | null>;
  /** Put on the scrolling element: remembers whether the reader is at the end. */
  onScroll: () => void;
  /** Follow from now on, wherever the reader is (his own question). */
  follow: () => void;
}

/**
 * @param content changes whenever the text in the element grows (a new message, step, or answer).
 * @param opened changes when a different conversation is opened — that one starts at its end.
 */
export function useFollowBottom<T extends HTMLElement>(content: string, opened: string | null): FollowBottom<T> {
  const ref = useRef<T>(null);
  const atEnd = useRef(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_END_PX;
  }, []);

  const follow = useCallback(() => {
    atEnd.current = true;
  }, []);

  useLayoutEffect(() => {
    atEnd.current = true;
  }, [opened]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && atEnd.current) el.scrollTop = el.scrollHeight;
  }, [content, opened]);

  return { ref, onScroll, follow };
}
