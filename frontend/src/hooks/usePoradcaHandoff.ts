// usePoradcaHandoff — the box of Riadiace centrum that takes the Manažér's text right now also takes the
// instruction handed off from Poradca (DEV-22). See `@/lib/poradcaHandoff` for why the handoff is pending
// and not written straight into one box.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  HANDOFF_EVENT,
  appendInstruction,
  draftCameFromPoradca,
  forgetPoradcaOrigin,
  markFromPoradca,
  takeHandoff,
  type HandoffSurface,
} from "@/lib/poradcaHandoff";

interface Options {
  surface: HandoffSurface;
  versionId: string | null | undefined;
  /** This box takes the Manažér's text right now — rendered, not collapsed, not locked, build state known. */
  live: boolean;
  text: string;
  setText: (value: string) => void;
  /** The draft was restored from storage and not edited yet (useDraft). */
  restored: boolean;
}

export interface PoradcaHandoff {
  /** The box shows Poradca's instruction the Manažér has not touched yet — label it as such. */
  fromPoradca: boolean;
  /** The text is his now (edited or sent): drop the label and the origin mark. */
  dismiss: () => void;
}

export function usePoradcaHandoff({ surface, versionId, live, text, setText, restored }: Options): PoradcaHandoff {
  const [fromPoradca, setFromPoradca] = useState<boolean>(
    () => restored && draftCameFromPoradca(versionId, surface),
  );
  // The event handler must append to the CURRENT text, not to the text of the render that subscribed.
  const textRef = useRef(text);
  textRef.current = text;

  useEffect(() => {
    if (!live || !versionId) return;
    const take = () => {
      const incoming = takeHandoff(versionId);
      if (!incoming) return;
      const next = appendInstruction(textRef.current, incoming);
      textRef.current = next;
      setText(next);
      markFromPoradca(versionId, surface);
      setFromPoradca(true);
    };
    take(); // handed off before this box became live (another screen, or the board was still loading)
    const onHandoff = (e: Event) => {
      if ((e as CustomEvent<{ versionId?: string }>).detail?.versionId === versionId) take();
    };
    window.addEventListener(HANDOFF_EVENT, onHandoff);
    return () => window.removeEventListener(HANDOFF_EVENT, onHandoff);
  }, [live, versionId, surface, setText]);

  const dismiss = useCallback(() => {
    setFromPoradca(false);
    forgetPoradcaOrigin(versionId, surface);
  }, [versionId, surface]);

  return { fromPoradca, dismiss };
}
