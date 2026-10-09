/**
 * Decides "what state is the Install app button in".
 *
 * PURE function that never touches the browser, precisely so tests can walk the whole
 * table and so it can move into `nex-shared`. Everything that knows about the browser
 * lives in `installPrompt.ts`.
 */

export type InstallState =
  /** 1 - the live preview is running; installing is impossible and nothing is faked. */
  | "preview"
  /** 2 - the app runs in an installed window. */
  | "standalone"
  /** 3 - the browser's install offer is captured; a click triggers it. */
  | "ready"
  /** 4 - we saw the installation finish (remembered). */
  | "installed"
  /** 5 - the browser does not know this way of installing (Firefox, Safari). */
  | "unsupported"
  /** 6 - the page does not run on a secure address. */
  | "insecure"
  /** 7 - the browser has not offered yet, or the offer was dismissed. */
  | "unavailable";

export interface InstallSignals {
  /** Is the live preview running? Then installing is impossible and nothing is faked. */
  isPreview: boolean;
  /** Does the app run in an installed window, not in a browser tab? */
  isStandalone: boolean;
  /** Do we hold a CAPTURED offer that can be triggered? Not "the browser can do it". */
  hasPrompt: boolean;
  /** We saw the installation finish. This is memory, not a fact about this moment. */
  rememberedInstalled: boolean;
  /** The browser KNOWS this way of installing. Says nothing about whether it offers now. */
  supportsPromptApi: boolean;
  /**
   * ONLY when we know it FOR SURE. An environment that does not know `isSecureContext`
   * sends `false` here: "we do not know" is never passed off as "not secure".
   */
  isInsecure: boolean;
}

/**
 * The order is BINDING and every line has a factual reason. Unlike the guard in
 * `versionCheck.ts`, the order is OBSERVABLE here: every branch returns something
 * different, so tests really do guard it.
 */
export function decideInstallState(s: InstallSignals): InstallState {
  // Preview is first: functionality must never be faked in it.
  if (s.isPreview) return "preview";

  // Whoever already has the app in its own window needs to hear nothing else.
  if (s.isStandalone) return "standalone";

  // A fresh browser offer beats the remembered installation: the browser offers only to
  // someone who does NOT have the app installed. (This also corrects the memory itself.)
  if (s.hasPrompt) return "ready";

  // Only here, because it is our MEMORY, not a fresh fact: it holds only until the
  // browser says something newer. It stands before the rest because "installed" is a
  // concrete answer, and a concrete answer beats "we do not know the reason".
  if (s.rememberedInstalled) return "installed";

  // "The browser cannot do it" BEFORE "insecure address": in desktop Firefox a sentence
  // about https would mislead, because the app cannot be installed there even over https.
  if (!s.supportsPromptApi) return "unsupported";

  // The only reason we know for SURE, so we may say it aloud. It stands here because a
  // sentence about https does not help a browser without support (above), and before
  // the rest so the certainty is never dropped into the "we do not know" bucket.
  if (s.isInsecure) return "insecure";

  // The rest: we do not know the reason, so we claim none and offer the manual way.
  return "unavailable";
}

/**
 * Is installation DONE, or is it only NOT POSSIBLE right now? This is what the whole
 * "hide or not hide" question stands on:
 *  - **not possible right now** (insecure address, browser has not offered yet, this
 *    browser cannot do it) - the button STAYS and states the reason. Hidden, it would
 *    look like an app bug;
 *  - **done** (the app runs in its own window, or is installed on this computer) - the
 *    button is NOT rendered at all. Offering a person what they already have is no help.
 *
 * Hiding is safe by design, not by luck: `decideInstallState` gives a fresh browser
 * offer priority over the remembered installation. When the app is NOT installed (other
 * profile, cleared data), the browser fires the offer again, the state stops being done
 * and the button returns on its own. That priority is the safety net of this function
 * and must not be broken.
 */
export function isInstallDone(state: InstallState): boolean {
  return state === "standalone" || state === "installed";
}
