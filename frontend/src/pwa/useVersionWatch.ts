import { useCallback, useEffect, useRef, useState } from "react";
import { BUILD_ID } from "./buildId";
import { SERVER_BACK_EVENT } from "./serverBack";
import {
  clearGuardState,
  decide,
  readGuardState,
  recordAutoReload,
  type VersionVerdict,
} from "./versionCheck";

/** Default interval of the repeated check while the app runs. */
const DEFAULT_INTERVAL_MS = 15 * 60 * 1000;

export interface VersionWatchOptions {
  intervalMs?: number;
  versionUrl?: string;
  /**
   * May the app reload itself at startup? Switched off in a live preview so the bar
   * can be shown without the preview refreshing constantly.
   */
  autoReload?: boolean;
}

/**
 * Asks the server whether a newer version exists.
 *
 * - at startup -> when newer, the app reloads itself silently,
 * - on the window returning to the foreground, on the person switching to it, on its connection to the server
 *   coming back (DEV-63: every deploy restarts the server) and periodically -> the app is NOT forced, it offers
 *   a bar.
 */
export function useVersionWatch(options?: VersionWatchOptions): {
  updateAvailable: boolean;
  applyUpdate: () => void;
  dismiss: () => void;
} {
  const {
    intervalMs = DEFAULT_INTERVAL_MS,
    versionUrl = "/version.json",
    autoReload = true,
  } = options ?? {};

  const [updateAvailable, setUpdateAvailable] = useState(false);
  // True while the hook lives. After unmount neither state may be set nor the page
  // reloaded - a reload after leaving the screen would only confuse the person.
  const alive = useRef(true);

  const check = useCallback(
    async (phase: "startup" | "runtime") => {
      let serverBuildId: string | null = null;
      try {
        // Explicit ban on using a cached copy - belt and braces alongside the server headers.
        const res = await fetch(versionUrl, { cache: "no-store" });
        if (res.ok) {
          const body = (await res.json()) as { buildId?: unknown };
          serverBuildId = typeof body.buildId === "string" ? body.buildId : null;
        }
      } catch {
        serverBuildId = null; // an unreachable server must not block startup
      }

      if (!alive.current) return;

      const verdict: VersionVerdict = decide({
        localBuildId: BUILD_ID,
        serverBuildId,
        ...readGuardState(),
        phase,
      });

      if (verdict === "current") {
        clearGuardState();
        setUpdateAvailable(false);
        return;
      }
      if (verdict === "unknown") return;

      if (verdict === "reload" && autoReload && serverBuildId) {
        // Reload ONLY when the guard state was saved successfully. Otherwise the counter
        // would not increase, the ceiling would not kick in and the app would reload
        // forever - better to offer the bar.
        if (recordAutoReload(serverBuildId)) {
          window.location.reload();
          return;
        }
      }

      // "offer" - and in the preview also "reload": leave the decision to the person.
      // The bar can be dismissed; it appears again at the next check.
      setUpdateAvailable(true);
    },
    [autoReload, versionUrl],
  );

  useEffect(() => {
    alive.current = true;
    void check("startup");

    const onVisible = () => {
      if (document.visibilityState === "visible") void check("runtime");
    };
    // DEV-63: a window that stays on screen never becomes "visible" again — switching between two open windows is
    // a focus change, and a deploy shows itself as the server connection coming back.
    const onAttention = () => void check("runtime");
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onAttention);
    window.addEventListener(SERVER_BACK_EVENT, onAttention);
    const timer = setInterval(() => void check("runtime"), intervalMs);

    return () => {
      alive.current = false;
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onAttention);
      window.removeEventListener(SERVER_BACK_EVENT, onAttention);
      clearInterval(timer);
    };
  }, [check, intervalMs]);

  const applyUpdate = useCallback(() => window.location.reload(), []);
  const dismiss = useCallback(() => setUpdateAvailable(false), []);

  return { updateAvailable, applyUpdate, dismiss };
}
