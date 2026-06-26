/**
 * Manažér presence ("away") store — E6 Telegram presence toggle (CR-NS-038).
 *
 * Today the cockpit pings the Manažér on Telegram only when no Manažér holds a
 * live board WebSocket. ``isAway`` is the explicit "I stepped away from the
 * computer" annotation on that existing presence: when ``true``, an
 * ``awaiting_manazer`` / ``blocked`` event pings Telegram **even with the board
 * open** (the away state is sent to the backend over the board WS, which flips the
 * connection to non-active in the notify gate).
 *
 * Persisted (Zustand ``persist``, key ``nex-presence``) so "away" survives a
 * reload — the safe direction (a refresh must not silently re-enable pings while
 * the Manažér is still away). It is reset to ``false`` on login (see
 * ``authStore.login``) so "away" never carries silently into a fresh session.
 *
 * Manual revert (Director-approved 2026-06-12): the Manažér toggles back to "at
 * computer" themselves — there is intentionally NO auto-clear on board interaction.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface PresenceState {
  /** ``true`` = the Director marked themselves away (board may be open). Default ``false``. */
  isAway: boolean;
  setIsAway: (away: boolean) => void;
}

export const usePresenceStore = create<PresenceState>()(
  persist(
    (set) => ({
      isAway: false,
      setIsAway: (away) => set({ isAway: away }),
    }),
    {
      name: "nex-presence",
      partialize: (state) => ({ isAway: state.isAway }),
    },
  ),
);
