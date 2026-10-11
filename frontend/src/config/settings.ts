/**
 * Runtime configuration of the cockpit frontend (KB icc/CLEAN_CODE.md §4) — intervals and limits in one place.
 */
export const SETTINGS = {
  /**
   * DEV-63: how often an open window asks the server whether a newer cockpit version is out — besides asking at load,
   * when the person switches to the window and when its connection to the server comes back. `/version.json` is a
   * tiny static file; a window left on screen learns about a deploy within a minute, not fifteen.
   */
  VERSION_CHECK_INTERVAL_MS: 60 * 1000,
} as const;
