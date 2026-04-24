import { useLocation, useNavigate } from "react-router-dom";

/**
 * Tab header rendered on top of :class:`ProfSpecPage` and
 * :class:`UIDesignPage` — the two sub-pages of the parallel Krok 2
 * "Solution" phase. Lets the user switch between
 * ``Vývojová dokumentácia`` and ``Návrh UI dizajnu`` without going
 * through the version overview.
 *
 * The sidebar "Solution" entry deliberately lands on ``/profspec``
 * by default because 2A precedes 2B in the approval order.
 */
export default function SolutionTabs({
  slug,
  versionId,
}: {
  slug: string;
  versionId: string;
}) {
  const location = useLocation();
  const navigate = useNavigate();

  const activeTab: "profspec" | "uidesign" = location.pathname.endsWith("/uidesign")
    ? "uidesign"
    : "profspec";

  const tabCls = (isActive: boolean) =>
    `px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
      isActive
        ? "border-primary-400 text-primary-300"
        : "border-transparent text-slate-500 hover:text-slate-300"
    }`;

  return (
    <div className="flex-shrink-0 bg-slate-900/30 border-b border-slate-800 flex items-center gap-1 px-5">
      <button
        onClick={() => navigate(`/projects/${slug}/versions/${versionId}/profspec`)}
        className={tabCls(activeTab === "profspec")}
      >
        Vývojová dokumentácia
      </button>
      <button
        onClick={() => navigate(`/projects/${slug}/versions/${versionId}/uidesign`)}
        className={tabCls(activeTab === "uidesign")}
      >
        Návrh UI dizajnu
      </button>
    </div>
  );
}
