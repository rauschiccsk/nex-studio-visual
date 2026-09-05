import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import { getVersion } from "@/services/api/versions";
import { useActiveContextStore } from "@/store/activeContextStore";

/**
 * Open a version's cockpit — pin it as the active context and go to the build board (ICCINT-62).
 *
 * Starting a fast fix is the ONLY action in the cockpit that creates a new version; every other verb continues
 * the one already open. So it is the only place where "stay where you are" is wrong — and it was getting
 * missed. The native "Rýchla oprava" button did this last step; the Dedo proposal bar, added later as a second
 * entry into the SAME lane, did not: the build started and the Manažér was left standing on the previous
 * version, watching the bar disappear and nothing happen (05.09.2026, reported twice in one evening).
 *
 * Extracted so both entries share the step and a third one cannot quietly omit it again.
 *
 * ``project`` is pinned FIRST when given, because pinning a project clears the version slot — pass it only
 * when moving to a DIFFERENT project's version; from inside a build the project is already pinned.
 */
export function useOpenVersionCockpit() {
  const navigate = useNavigate();
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);

  return useCallback(
    async (versionId: string, project?: { slug: string; name: string }) => {
      const version = await getVersion(versionId);
      if (project) setSelectedProject(project);
      setSelectedVersion({ versionId: version.id, versionNumber: version.version_number });
      navigate("/vyvoj");
    },
    [navigate, setSelectedProject, setSelectedVersion],
  );
}
