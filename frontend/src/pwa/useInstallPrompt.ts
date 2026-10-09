import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { guideFor, type InstallGuide } from "./installGuides";
import {
  readInstallSignals,
  runInstallPrompt,
  startInstallCapture,
  subscribeInstall,
  wasPromptDismissed,
} from "./installPrompt";
import { decideInstallState, type InstallSignals, type InstallState } from "./installState";
import { bubbleFor, dialogFor, type InstallDialogContent } from "./installTexts";

export interface InstallPromptView {
  state: InstallState;
  /** Sentence for the bubble. Empty only in the `ready` state. */
  bubble: string;
  /** Open dialog with an explanation or guide; `null` = closed. */
  dialog: InstallDialogContent | null;
  onClick: () => void;
  closeDialog: () => void;
}

/**
 * Bridge between the offer capture and the screen.
 */
export function useInstallPrompt(appName: string): InstallPromptView {
  const [signals, setSignals] = useState<InstallSignals>(readInstallSignals);
  const [dismissed, setDismissed] = useState<boolean>(wasPromptDismissed);
  const [dialog, setDialog] = useState<InstallDialogContent | null>(null);

  // The browser does not change while the app runs, so the guide needs resolving only once.
  const guide: InstallGuide = useMemo(
    () => guideFor(typeof navigator === "undefined" ? "" : navigator.userAgent),
    [],
  );

  // The confirmation is shown AT MOST ONCE per app run - the browser may send the
  // "installation finished" event repeatedly.
  //
  // It is deliberately NOT tied to the remembered marker: the confirmation belongs to
  // every installation that really happened. If the ref were filled from memory, a person
  // who once had the app would get not a word about where to find it after a real
  // installation - exactly the loss that the whole "notice from the event, not from the
  // write" design guards against.
  const confirmationShown = useRef(false);

  useEffect(() => {
    // Safety net: `main.tsx` enables the capture before rendering. Should it ever get
    // lost there, the app at least does not end up without it entirely.
    startInstallCapture();

    const off = subscribeInstall((notice) => {
      setSignals(readInstallSignals());
      setDismissed(wasPromptDismissed());
      if (notice === "installed" && !confirmationShown.current) {
        confirmationShown.current = true;
        setDialog(dialogFor("installed", { appName, guide, dismissed: false, justInstalled: true }));
      }
    });

    // CATCH-UP: between the first read (`useState`) and subscribing to changes there is a
    // window in which the offer can arrive - and nobody would announce such a change.
    // Without this line the button would stay mute in a rare but real case.
    setSignals(readInstallSignals());
    setDismissed(wasPromptDismissed());

    return off;
  }, [appName, guide]);

  const state = useMemo(() => decideInstallState(signals), [signals]);
  const bubble = useMemo(() => bubbleFor(state, { dismissed }), [state, dismissed]);

  const onClick = useCallback(() => {
    if (state === "ready") {
      // NO `await` before `prompt()` - the browser requires a user gesture and after the
      // first wait it no longer holds it.
      void runInstallPrompt().then((outcome) => {
        // `dismissed` -> no dialog. The person just said "no"; opening something at once
        // would be exactly the nagging the specification forbids. They get the guide on
        // the next click.
        if (outcome === "unavailable") {
          setDialog(dialogFor("unavailable", { appName, guide, dismissed: wasPromptDismissed() }));
        }
      });
      return;
    }
    setDialog(dialogFor(state, { appName, guide, dismissed }));
  }, [state, appName, guide, dismissed]);

  const closeDialog = useCallback(() => setDialog(null), []);

  return { state, bubble, dialog, onClick, closeDialog };
}
