import { useEffect, useId } from "react";
import { Button } from "nex-shared";

import type { InstallGuide } from "./installGuides";

interface InstallHelpDialogProps {
  title: string;
  lines: string[];
  guide?: InstallGuide;
  onClose: () => void;
}

/**
 * Dialog with an explanation or a manual guide.
 *
 * Deliberately NOT `ConfirmDialog`: that one confirms a dangerous operation (red button
 * and "Cancel"). Here nothing is confirmed or cancelled, the dialog only informs. The
 * look (overlay, card) is borrowed from it so the app looks like one whole.
 */
export function InstallHelpDialog({ title, lines, guide, onClose }: InstallHelpDialogProps) {
  // The heading id is not hard-coded - a hard-coded id is a bug that shows only once the
  // component is repeated on the screen.
  const titleId = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      {/* A click inside the card does not close the dialog - it would close while selecting text. */}
      <div
        className="w-full max-w-md rounded-lg border border-border-default bg-canvas p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId} className="mb-3 text-lg font-bold text-text-primary">
          {title}
        </h2>

        {lines.map((line) => (
          <p key={line} className="mb-3 text-sm text-text-secondary">
            {line}
          </p>
        ))}

        {guide &&
          (guide.impossible ? (
            // When installing is impossible these are not steps but sentences. Numbering
            // would promise a procedure that does not exist.
            guide.steps.map((step) => (
              <p key={step} className="mb-3 text-sm text-text-secondary">
                {step}
              </p>
            ))
          ) : (
            <ol className="mb-4 list-decimal space-y-2 pl-5 text-sm text-text-secondary">
              {guide.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          ))}

        <div className="flex justify-end">
          {/* `autoFocus` instead of `ref`: the shared button does not accept `ref`, and focus
              on open is the only thing needed from it here. */}
          <Button autoFocus variant="primary" size="sm" onClick={onClose} type="button">
            Rozumiem
          </Button>
        </div>
      </div>
    </div>
  );
}
