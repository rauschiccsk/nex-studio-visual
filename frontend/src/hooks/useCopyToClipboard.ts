import { useState, useCallback } from "react";

/** Visual feedback duration after a successful copy. */
const COPY_FEEDBACK_DURATION_MS = 2000;

/**
 * Hook for clipboard copy with visual feedback.
 * Returns ``[copyFn, isCopied]`` — ``isCopied`` resets after ``resetDelay`` ms.
 *
 * Ported 1:1 from NEX Command (`frontend/src/hooks/useCopyToClipboard.ts`)
 * per Director mandate 2026-05-07 (M1.D milestone).
 */
export function useCopyToClipboard(
  resetDelay = COPY_FEEDBACK_DURATION_MS,
): [(text: string) => Promise<void>, boolean] {
  const [isCopied, setIsCopied] = useState(false);

  const copy = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), resetDelay);
      } catch {
        // Fallback for older browsers / Electron
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), resetDelay);
      }
    },
    [resetDelay],
  );

  return [copy, isCopied];
}
