import { useEffect } from "react";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextStore } from "@/store/activeContextStore";

/**
 * Keep :mod:`activeContextStore` in sync with the currently loaded
 * project + version on pages like ``VersionDetailPage`` and every
 * ``pages/step/*`` page.
 *
 * Writes to the two independent slots (``selectedProject`` and
 * ``selectedVersion``). The project slot is also writable from
 * :file:`pages/ProjectsPage.tsx` via the Pin icon — that path is the
 * canonical "user picked a project to work on" action, while this
 * hook handles the implicit "user is currently looking at this
 * project + version" sync.
 *
 * The hook re-runs when either ``project`` or ``version`` changes.
 * Pass ``null`` while data is loading; the hook is a no-op for
 * partial inputs.
 */
export function useActiveContextSync(
  project: ProjectRead | null,
  version: Version | null,
): void {
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);

  useEffect(() => {
    if (project) {
      setSelectedProject({ slug: project.slug, name: project.name });
    }
    if (version) {
      setSelectedVersion({
        versionId: version.id,
        versionNumber: version.version_number,
      });
    }
  }, [project, version, setSelectedProject, setSelectedVersion]);
}
