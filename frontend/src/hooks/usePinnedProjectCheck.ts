import { useEffect } from "react";

import { listProjectsApi } from "@/services/api/projects";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useAuthStore } from "@/store/authStore";

/**
 * DEV-262 — a pinned project the person no longer has is unpinned, and the sidebar says why.
 *
 * 30.10.2026 the Director moved a project to another user as admin; it stayed pinned for its former owner. The pin
 * lives in the browser (activeContextStore) and nothing compared it with what the person may open. This asks the
 * server for the ONE pinned project by its slug — the same per-user filter as the project list — when the cockpit
 * opens, when the pin changes and when the window comes back into view. ``total: 0`` unpins it; a failed question
 * unpins nothing: an outage is not "no longer yours".
 */
export function usePinnedProjectCheck(): void {
  const userId = useAuthStore((s) => s.user?.id);
  const pinned = useActiveContextStore((s) => s.selectedProject);
  const dropLostProject = useActiveContextStore((s) => s.dropLostProject);
  const slug = pinned?.slug;
  const name = pinned?.name;

  useEffect(() => {
    if (!userId || !slug || !name) return;
    let cancelled = false;
    const check = () => {
      listProjectsApi({ slug, limit: 1 })
        .then((res) => {
          if (!cancelled && res.total === 0) dropLostProject(name);
        })
        .catch(() => undefined);
    };
    check();
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [userId, slug, name, dropLostProject]);
}
