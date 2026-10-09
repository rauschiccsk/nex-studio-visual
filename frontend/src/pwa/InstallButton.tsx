import { useCallback, useId, useRef, useState } from "react";
import { Button } from "nex-shared";

import { InstallHelpDialog } from "./InstallHelpDialog";
import { isInstallDone } from "./installState";
import { useInstallPrompt } from "./useInstallPrompt";

interface InstallButtonProps {
  /** App name used in the texts - the module itself does not know which app it is in. */
  appName: string;
}

/**
 * Outline and text colour come **from a shared kit token**, never as a literal value,
 * otherwise they would drift when the palette changes. They are set directly on the
 * element on purpose: the shared button sets its own text colour, and which of two
 * equally strong classes wins depends on the order in the built stylesheet, which
 * cannot be verified here without a browser. Set this way, no ordering decides it.
 */
const ACCENT = {
  color: "var(--color-accent-primary)",
  borderColor: "var(--color-accent-primary)",
} as const;

/**
 * The "Install app" button.
 *
 * Four rules that must not be broken:
 *  1. **outlined in the accent colour** - outline and text in the accent, transparent
 *     background. Measured against the page background: **4.00 : 1** in dark and
 *     **6.01 : 1** in light mode (the standard for a recognisable control is 3 : 1).
 *     The previous grey form had 1.42 : 1, not even half the standard;
 *  2. **deliberately NOT a solid accent fill** - the "Spustiť" buttons on cards own
 *     that. This app is a launcher and launching is its daily activity; installation
 *     must not outshout it;
 *  3. **never `disabled`** - a disabled button accepts neither clicks nor keyboard
 *     focus, and a touch screen has no "hover", so the person would never reach the
 *     explanation. It is dimmed only visually;
 *  4. **only what is DONE gets hidden** - while installation is merely not possible
 *     right now (insecure address, browser has not offered yet, this browser cannot do
 *     it) the button stays and states the reason: hidden, it would look like an app
 *     bug. But when installation is DONE - the app runs in its own window or is
 *     installed on this computer (`isInstallDone`) - it is not rendered AT ALL: not
 *     greyed out, not relabelled, not more transparent. Offering a person what they
 *     already have is not an answer but a defect.
 */
export function InstallButton({ appName }: InstallButtonProps) {
  const { state, bubble, dialog, onClick, closeDialog } = useInstallPrompt(appName);
  const [bubbleOpen, setBubbleOpen] = useState(false);
  // The bubble id is not hard-coded - two identical ids on one screen are a bug that
  // shows only once the component is repeated somewhere.
  const hintId = useId();
  const wrapper = useRef<HTMLDivElement>(null);

  const closeAndRefocus = useCallback(() => {
    closeDialog();
    // Focus returns to the button. Without it a keyboard user would land at the top of
    // the page after closing the dialog and have to tab back - opening the dialog would
    // punish them for reading the guide. (The dialog is still in the tree at this
    // moment, but the button comes first in order.)
    wrapper.current?.querySelector("button")?.focus();
  }, [closeDialog]);

  const isReady = state === "ready";
  const showBubble = bubble !== "";

  // Finished installation: neither the button nor the bubble reach the screen. The
  // dialog MUST still render - after a completed installation the app speaks up through
  // it and says where to find the app. The offer is hidden, not the answer. (Focus goes
  // nowhere: there is nowhere to go.)
  if (isInstallDone(state)) {
    return dialog ? (
      <InstallHelpDialog
        title={dialog.title}
        lines={dialog.lines}
        guide={dialog.guide}
        onClose={closeDialog}
      />
    ) : null;
  }

  return (
    <div
      ref={wrapper}
      className="relative inline-block"
      onMouseEnter={() => setBubbleOpen(true)}
      onMouseLeave={() => setBubbleOpen(false)}
      // Keyboard focus must open the bubble too - otherwise it does not exist for some people.
      onFocus={() => setBubbleOpen(true)}
      onBlur={() => setBubbleOpen(false)}
    >
      <Button
        // `ghost` = transparent background from the shared kit; outline and colour are added below.
        variant="ghost"
        size="md"
        type="button"
        onClick={onClick}
        aria-describedby={showBubble ? hintId : undefined}
        // Dimmed states keep the same shape and colour, just more transparent. The 0.85
        // value is not a guess: at 0.7 the dark-mode contrast fell to 2.59 : 1, BELOW the
        // standard; 0.85 holds 3.23 : 1. Transparency can only dim as far as the button
        // stays recognisable.
        className={`border ${isReady ? "" : "opacity-[0.85]"}`}
        style={ACCENT}
      >
        Nainštalovať aplikáciu
      </Button>

      {showBubble && (
        // The bubble is ALWAYS in the tree, only its opacity changes. `hidden` or
        // `visibility` cannot be used - they would remove the text from the
        // accessibility tree and `aria-describedby` would point at nothing.
        <span
          id={hintId}
          role="tooltip"
          className={`absolute left-0 top-full z-20 mt-1 w-72 rounded-md border border-border-default bg-surface-elevated px-3 py-2 text-xs leading-snug text-text-secondary shadow-lg transition-opacity ${
            bubbleOpen ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        >
          {bubble}
        </span>
      )}

      {dialog && (
        <InstallHelpDialog
          title={dialog.title}
          lines={dialog.lines}
          guide={dialog.guide}
          onClose={closeAndRefocus}
        />
      )}
    </div>
  );
}
