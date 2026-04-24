import { useEffect } from "react";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextStore } from "@/store/activeContextStore";

/**
 * Keep :mod:`activeContextStore` in sync with the currently loaded
 * project + version on pages like ``VersionDetailPage`` and every
 * ``pages/step/*`` page.
 *
 * The sidebar reads ``activeContextStore`` to build one-click
 * pipeline shortcuts from anywhere in the app — so every page that
 * knows which verzia the user is on must keep the store fresh.
 *
 * Pass ``null`` while data is still loading; the hook re-runs when
 * both ``project`` and ``version`` resolve.
 */
export function useActiveContextSync(
  project: ProjectRead | null,
  version: Version | null,
): void {
  const setActiveContext = useActiveContextStore((s) => s.setActiveContext);

  useEffect(() => {
    if (!project || !version) return;
    setActiveContext({
      slug: project.slug,
      versionId: version.id,
      projectName: project.name,
      versionNumber: version.version_number,
    });
  }, [project, version, setActiveContext]);
}
