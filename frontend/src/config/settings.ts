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
  /**
   * DEV-378: how many installations the „Inštalácie“ page asks at once whether they live — a few at a time, never all
   * fifty customers' servers in one burst; the server shares each answer for a while.
   */
  INSTALLATION_STATUS_CONCURRENCY: 4,
} as const;
