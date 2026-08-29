// useDraft — a half-written message survives leaving the screen (ICCINT-30).
//
// Found by the Director 26.08.2026 while working with the AI Agent on nex-productcatalogs: he typed a message,
// clicked over to Dokumenty to check what he was about to say, came back — and the box was empty. Nothing had
// warned him; the text was simply gone.
//
// The cause was in every writing surface at once, not just the chat: each held its text in `useState`, and a
// route change unmounts the component. Checked at the time — NONE of the five persisted anything. The worst
// was not the chat: it was DedoProposalBar, where he edits a proposal before sending. Leave, come back, and
// the edits are gone while the ORIGINAL text is still there to send — so he could send a version he had
// already decided against, believing it was his.
//
// What makes this worth fixing rather than shrugging at: clicking over to Dokumenty is not a detour from the
// work, it IS the work — he goes to check what he is writing about. The app punished him for the exact
// behaviour we want from him, and did it silently.
//
// The draft is cleared on a SUCCESSFUL send, never on leaving. Keyed per surface AND per build, so two
// half-written texts can never bleed into one another.

import { useCallback, useEffect, useRef, useState } from "react";

/** Storage key for one writing surface of one build. `null` → no persistence (nothing to key it to). */
export function draftKey(surface: string, versionId?: string | null): string | null {
  return versionId ? `nex.draft.${surface}.${versionId}` : null;
}

export interface Draft {
  text: string;
  setText: (value: string) => void;
  /** Drop the draft — call ONLY after the text actually went somewhere. */
  clear: () => void;
  /** True while showing text restored from a previous visit and not yet edited. The surface says so: text
   *  that appears by itself must be recognisable as *his own earlier draft*, not as something someone
   *  else wrote into his box. */
  restored: boolean;
}

function read(key: string | null): string {
  if (!key) return "";
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return ""; // storage disabled/full — a draft is a convenience, never a reason to break the screen
  }
}

export function useDraft(key: string | null): Draft {
  const [text, setTextState] = useState<string>(() => read(key));
  const [restored, setRestored] = useState<boolean>(() => read(key).length > 0);
  // The key changes when the Manažér switches build; re-read rather than carry the previous one over.
  const lastKey = useRef(key);

  useEffect(() => {
    if (lastKey.current === key) return;
    lastKey.current = key;
    const stored = read(key);
    setTextState(stored);
    setRestored(stored.length > 0);
  }, [key]);

  const setText = useCallback(
    (value: string) => {
      setTextState(value);
      setRestored(false); // he is typing now; it is his current text, not a restored one
      if (!key) return;
      try {
        if (value) window.localStorage.setItem(key, value);
        else window.localStorage.removeItem(key);
      } catch {
        /* storage unavailable — keep the typing working */
      }
    },
    [key],
  );

  const clear = useCallback(() => {
    setTextState("");
    setRestored(false);
    if (!key) return;
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  }, [key]);

  return { text, setText, clear, restored };
}
