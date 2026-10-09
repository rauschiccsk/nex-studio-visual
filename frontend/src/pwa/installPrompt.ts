/**
 * Capture of the browser's install offer.
 *
 * This is the ONLY layer that knows about the browser. State lives in the module, NOT
 * in React: the offer arrives before the app renders, and component state would lose it.
 *
 * The browser gives the offer EXACTLY ONCE, at a moment of its own choosing. If the app
 * does not capture it then, it is gone for good and the button would have nothing to
 * trigger - hence enabling the capture is the first command the app executes (`main.tsx`).
 */

import type { InstallSignals } from "./installState";

/**
 * This event type does NOT exist in the TypeScript standard library, so we declare it
 * ourselves. It is the only place in the module with a type assertion.
 */
export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

export type InstallPromptOutcome = "accepted" | "dismissed" | "unavailable";

/** Marker "we saw the installation finish". The value says nothing about the person. */
export const INSTALLED_KEY = "nex_nex-studio-visual_installed";

/** What just happened. `"installed"` is the only change the app reacts to with a dialog. */
export type InstallNotice = "installed" | undefined;

let deferred: BeforeInstallPromptEvent | null = null;
let dismissed = false;
let started = false;
const listeners = new Set<(notice: InstallNotice) => void>();

function notify(notice?: InstallNotice): void {
  for (const fn of listeners) fn(notice);
}

/**
 * Turns the listeners on. IDEMPOTENT: in `StrictMode` effects run twice, and two
 * handlers for the same event would get in each other's way.
 */
export function startInstallCapture(): void {
  if (started || typeof window === "undefined") return;
  started = true;

  window.addEventListener("beforeinstallprompt", (e) => {
    // Without this, the browser on a phone shows its own banner - exactly the nagging
    // the specification forbids.
    e.preventDefault();
    // By VALUE, not by event name - the same rule as everywhere in this module. The offer
    // is usable only when it really carries `prompt()`. If we accepted an event without
    // it, the app would look "ready" and the click would end in nothing: the very claim
    // it cannot fulfil. The browser banner is suppressed either way.
    if (typeof (e as Partial<BeforeInstallPromptEvent>).prompt !== "function") return;
    deferred = e as BeforeInstallPromptEvent;
    // The browser offers again, so the earlier "I closed it" no longer holds ...
    dismissed = false;
    // ... and above all: it offers ONLY to someone who does not have the app installed.
    // The remembered installation is therefore invalid - the app would otherwise claim
    // for years something that is long untrue.
    forgetInstalled();
    notify();
  });

  window.addEventListener("appinstalled", () => {
    deferred = null; // the offer is spent
    // Saving the marker may FAIL (private window). The confirmation therefore relies on
    // the event itself, not on a successful write - the person sees it either way.
    markInstalled();
    notify("installed");
  });
}

export function subscribeInstall(fn: (notice: InstallNotice) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function readInstallSignals(): InstallSignals {
  return {
    // The cockpit has no live preview of itself, so the preview state never applies.
    isPreview: false,
    isStandalone: isStandaloneWindow(),
    hasPrompt: deferred !== null,
    rememberedInstalled: readInstalledFlag(),
    supportsPromptApi: hasPromptApi(),
    // CAUTION: strictly `=== false`. `undefined` means "unknown", and unknown must never
    // pass for "not secure" - we do not claim a reason we do not know.
    isInsecure: window.isSecureContext === false,
  };
}

/** Did the person dismiss the browser offer in this window? Changes only the bubble sentence. */
export function wasPromptDismissed(): boolean {
  return dismissed;
}

/**
 * Triggers the browser offer.
 *
 * WARNING: the caller must call it DIRECTLY in the click handler - there must be no
 * `await` before `prompt()`. The browser requires a user gesture and after the first
 * wait it no longer "holds" it.
 */
export async function runInstallPrompt(): Promise<InstallPromptOutcome> {
  const e = deferred;
  // Discarded IMMEDIATELY: the browser allows the offer to be used once.
  deferred = null;
  notify();

  if (!e) return "unavailable";

  try {
    await e.prompt();
    const { outcome } = await e.userChoice;
    if (outcome === "dismissed") {
      dismissed = true;
      notify();
    }
    return outcome;
  } catch {
    // A click must never end in nothing - the caller shows the manual guide instead.
    return "unavailable";
  }
}

/**
 * Writes the marker for a finished installation.
 *
 * Returns `false` when writing FAILED. Storage can also fail SILENTLY (throws nothing
 * and stores nothing), so the write is verified by reading back, not by the absence of
 * an exception.
 */
export function markInstalled(): boolean {
  try {
    localStorage.setItem(INSTALLED_KEY, "1");
    return localStorage.getItem(INSTALLED_KEY) === "1";
  } catch {
    return false;
  }
}

export function forgetInstalled(): void {
  try {
    localStorage.removeItem(INSTALLED_KEY);
  } catch {
    // If deleting is impossible, so is writing - the marker does not exist anyway.
  }
}

function readInstalledFlag(): boolean {
  try {
    return localStorage.getItem(INSTALLED_KEY) === "1";
  } catch {
    return false;
  }
}

function isStandaloneWindow(): boolean {
  // By VALUE, not by key presence: `"matchMedia" in window` is true even where the value
  // is `undefined` (e.g. in the test environment). The same trap as with
  // `navigator.serviceWorker`.
  if (typeof window.matchMedia !== "function") return false;

  const modes = ["standalone", "minimal-ui", "window-controls-overlay", "fullscreen"];
  if (modes.some((m) => window.matchMedia(`(display-mode: ${m})`).matches)) return true;

  // iPhone and iPad do not report `display-mode` - they have their own flag.
  return (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

function hasPromptApi(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as Window & { onbeforeinstallprompt?: unknown };
  // Key AND value: in Chrome the property has the value `null`, in Firefox and Safari it
  // does not exist at all. If we err here, the person gets a manual guide - a harmless
  // direction.
  return "onbeforeinstallprompt" in window && w.onbeforeinstallprompt !== undefined;
}

/** FOR tests only: resets the module state. */
export function __resetInstallCapture(): void {
  deferred = null;
  dismissed = false;
  listeners.clear();
}

/** FOR tests only: injects a captured offer without a real browser event. */
export function __setDeferredPrompt(e: BeforeInstallPromptEvent | null): void {
  deferred = e;
  notify();
}
