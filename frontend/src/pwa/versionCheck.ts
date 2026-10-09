/**
 * Decides "is a newer version out there, and what to do about it".
 *
 * `decide()` is a PURE function without side effects, so it can be verified without a
 * browser. The guard state lives next to it in this module - not scattered across
 * callers, because the guard is one whole and must be verifiable as a whole.
 */

export type VersionVerdict = "current" | "reload" | "offer" | "unknown";

/**
 * Absolute ceiling of automatic reloads per window launch.
 * Overrides everything else - see `decide()`.
 */
export const MAX_AUTO_RELOADS = 2;

/** Fingerprint for which the app has already reloaded once. */
export const GUARD_KEY = "nex_pwa_reload_guard";
/** How many automatic reloads have already happened in this window. */
export const COUNT_KEY = "nex_pwa_reload_count";

export interface VersionInput {
  /** Fingerprint built into the currently running bundle. */
  localBuildId: string;
  /** Fingerprint reported by the server. `null` = could not be determined. */
  serverBuildId: string | null;
  /** Fingerprint for which the app has already reloaded once. */
  guard: string | null;
  /** How many automatic reloads have already happened in this window. */
  reloadCount: number;
  phase: "startup" | "runtime";
}

/**
 * The guard has TWO layers and the order is binding: the ceiling FIRST, the fingerprint
 * SECOND.
 *
 *  1. **Absolute ceiling** - at most `MAX_AUTO_RELOADS` reloads per window, whatever the
 *     server reports. It overrides because it must kick in even in situations nobody
 *     thought of in advance.
 *  2. **By fingerprint** - the same fingerprint is not silently reloaded for a second time.
 *
 * The second layer alone is NOT enough: when the server serves two different versions at
 * once (a version swap, several instances behind a load balancer), the fingerprints
 * alternate, the memory of the last one is used up on every alternation and the app
 * would reload forever - unusable, a worse failure than the one being prevented.
 *
 * It is kept nevertheless because it solves a different thing: when another version is
 * released during the day, its fingerprint differs and the silent reload goes through
 * normally.
 */
export function decide(input: VersionInput): VersionVerdict {
  const { localBuildId, serverBuildId, guard, reloadCount, phase } = input;

  // An unreachable server MUST NOT block startup.
  if (!serverBuildId) return "unknown";

  if (serverBuildId === localBuildId) return "current";

  // During work the app NEVER reloads itself - it could discard a half-filled form.
  if (phase !== "startup") return "offer";

  // The ceiling is written first on purpose - it is the layer that must kick in even
  // where the fingerprint cannot see. Today the order does not matter (both branches
  // return `offer`, so it is irrelevant which one fires), but if anyone changes the
  // verdict of either, the order starts to decide - and then the ceiling must prevail.
  if (reloadCount >= MAX_AUTO_RELOADS) return "offer";
  if (guard === serverBuildId) return "offer";

  return "reload";
}

// -- Guard state ------------------------------------------------------------
//
// `sessionStorage` is chosen on purpose: it survives a page reload (otherwise the guard
// would not work - after the reload it would know nothing about itself), but disappears
// with the window (otherwise the person could never get rid of its state).
//
// Access to it can throw (strict privacy settings, nested frame). Everything is
// therefore handled - and not by silence: when the state cannot be WRITTEN, the
// automatic reload is NOT performed. Otherwise the counter would never increase, the
// ceiling would not kick in and the app would reload forever.

export interface GuardState {
  guard: string | null;
  reloadCount: number;
}

/** Reads the guard state. When reading is impossible, pretends it is empty. */
export function readGuardState(): GuardState {
  try {
    const raw = sessionStorage.getItem(COUNT_KEY);
    const n = raw ? Number.parseInt(raw, 10) : 0;
    return {
      guard: sessionStorage.getItem(GUARD_KEY),
      reloadCount: Number.isFinite(n) && n > 0 ? n : 0,
    };
  } catch {
    return { guard: null, reloadCount: 0 };
  }
}

/**
 * Records that the app is about to reload automatically because of `serverBuildId`.
 *
 * Returns `false` when the state could NOT be saved - the caller then MUST NOT perform
 * the reload, because the ceiling would have no way to kick in.
 */
export function recordAutoReload(serverBuildId: string): boolean {
  try {
    const { reloadCount } = readGuardState();
    sessionStorage.setItem(GUARD_KEY, serverBuildId);
    sessionStorage.setItem(COUNT_KEY, String(reloadCount + 1));
    // Verify the write really survived - a silent failure would disable the ceiling.
    return readGuardState().reloadCount > reloadCount;
  } catch {
    return false;
  }
}

/** Resets both guard layers. Called when the fingerprints match. */
export function clearGuardState(): void {
  try {
    sessionStorage.removeItem(GUARD_KEY);
    sessionStorage.removeItem(COUNT_KEY);
  } catch {
    // If deleting is impossible, so is writing - the guard is inactive anyway.
  }
}
