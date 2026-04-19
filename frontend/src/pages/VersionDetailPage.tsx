/**
 * Version detail page — displays a single version with EPICs/Bugs tabs.
 *
 * Route: ``/projects/:slug/versions/:vid`` (DESIGN.md §3.1).
 *
 * Sections:
 *   - **Header**: version_number (h1), status badge, target_date, description
 *   - **Tabs**: EPICs table (title, status, progress) / Bugs table (title, severity, status)
 *   - **Footer**: Release Version button (``ri`` role, non-released, all epics done)
 *
 * The page fetches version data via {@link getVersion}.  EPICs and Bugs
 * are currently sourced from the version's aggregate counts — dedicated
 * per-version epic/bug list endpoints will be wired in a later feat.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { TaskPlanPanel } from "../components/tasks/TaskPlanPanel";
import VersionProgressBar from "../components/versions/VersionProgressBar";
import VersionStatusBadge from "../components/versions/VersionStatusBadge";
import ReleaseVersionDialog from "../components/versions/ReleaseVersionDialog";
import { ApiError } from "../services/api";
import { getVersion } from "../services/api/versions";
import type { Version } from "../types/version";
import { getUserRole } from "../utils/auth";
import { formatDate } from "../utils/format";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Tab = "epics" | "bugs" | "task_plan";

/** Placeholder row for the EPICs tab (until per-version epic list API). */
interface EpicRow {
  id: string;
  title: string;
  status: string;
  progress: number;
}

/** Placeholder row for the Bugs tab (until per-version bug list API). */
interface BugRow {
  id: string;
  title: string;
  severity: string;
  status: string;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

function VersionDetailPage() {
  const { vid } = useParams<{ slug: string; vid: string }>();

  const [version, setVersion] = useState<Version | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("epics");
  const [showReleaseDialog, setShowReleaseDialog] = useState(false);

  /* Placeholder lists — will be replaced by API calls in later feat */
  const [epics] = useState<EpicRow[]>([]);
  const [bugs] = useState<BugRow[]>([]);

  const role = getUserRole();
  const isRi = role === "ri";

  /* ---- Fetch version ---- */
  const load = useCallback(async () => {
    if (!vid) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getVersion(vid);
      setVersion(data);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Failed to load version");
      }
    } finally {
      setIsLoading(false);
    }
  }, [vid]);

  useEffect(() => {
    void load();
  }, [load]);

  /* ---- Release handler ---- */
  const handleReleased = useCallback((updated: Version) => {
    setVersion(updated);
    setShowReleaseDialog(false);
  }, []);

  /* ---- Render ---- */
  if (isLoading) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">Loading version…</p>;
  }

  if (error) {
    return (
      <div
        role="alert"
        className="rounded bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-400"
      >
        {error}
      </div>
    );
  }

  if (!version) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">Version not found.</p>
    );
  }

  const showReleaseButton = isRi && version.status !== "released";
  const releaseDisabled = version.epics_done < version.epic_count;

  return (
    <section className="space-y-6">
      {/* ---- Header ---- */}
      <div>
        <div className="flex items-center gap-3">
          <h1
            className="text-2xl font-bold text-gray-900 dark:text-gray-100"
            data-testid="version-title"
          >
            {version.version_number}
          </h1>
          <VersionStatusBadge status={version.status} />
        </div>

        {version.name && (
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">{version.name}</p>
        )}

        <div className="mt-2 flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400">
          <span data-testid="version-target-date">
            Target: {formatDate(version.target_date)}
          </span>
          {version.release_date && (
            <span data-testid="version-release-date">
              Released: {formatDate(version.release_date)}
            </span>
          )}
          <VersionProgressBar
            epicsDone={version.epics_done}
            epicCount={version.epic_count}
          />
        </div>

        {version.description && (
          <p className="mt-3 text-sm text-gray-700 dark:text-gray-300">{version.description}</p>
        )}
      </div>

      {/* ---- Tabs ---- */}
      <div>
        <div className="border-b border-gray-200 dark:border-gray-700">
          <nav className="-mb-px flex gap-4" aria-label="Tabs">
            <button
              type="button"
              onClick={() => setActiveTab("epics")}
              className={`whitespace-nowrap border-b-2 px-1 py-2 text-sm font-medium ${
                activeTab === "epics"
                  ? "border-primary-600 text-primary-600 dark:border-primary-400 dark:text-primary-400"
                  : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-300"
              }`}
              data-testid="tab-epics"
            >
              EPICs ({version.epic_count})
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("bugs")}
              className={`whitespace-nowrap border-b-2 px-1 py-2 text-sm font-medium ${
                activeTab === "bugs"
                  ? "border-primary-600 text-primary-600 dark:border-primary-400 dark:text-primary-400"
                  : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-300"
              }`}
              data-testid="tab-bugs"
            >
              Bugs ({version.bug_count})
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("task_plan")}
              className={`whitespace-nowrap border-b-2 px-1 py-2 text-sm font-medium ${
                activeTab === "task_plan"
                  ? "border-primary-600 text-primary-600 dark:border-primary-400 dark:text-primary-400"
                  : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-300"
              }`}
              data-testid="tab-task-plan"
            >
              Task Plan
            </button>
          </nav>
        </div>

        {/* ---- EPICs tab ---- */}
        <div className={`mt-4${activeTab !== "epics" ? " hidden" : ""}`} data-testid="epics-panel">
          {epics.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No EPICs loaded yet. Per-version EPIC listing will be available
              in a future update.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Title
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Status
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Progress
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-800">
                  {epics.map((e) => (
                    <tr key={e.id} className="hover:bg-gray-50 dark:bg-gray-900 dark:hover:bg-gray-800">
                      <td className="px-4 py-3 text-sm text-gray-900 dark:text-gray-100">
                        {e.title}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-800 dark:bg-gray-700 dark:text-gray-300">
                          {e.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                        {e.progress}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ---- Task Plan tab ---- */}
        <div className={`mt-4${activeTab !== "task_plan" ? " hidden" : ""}`} data-testid="task-plan-panel">
          <TaskPlanPanel versionId={version.id} canGenerate={isRi} />
        </div>

        {/* ---- Bugs tab ---- */}
        <div className={`mt-4${activeTab !== "bugs" ? " hidden" : ""}`} data-testid="bugs-panel">
          {bugs.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No bugs loaded yet. Per-version bug listing will be available
              in a future update.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Title
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Severity
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-800">
                  {bugs.map((b) => (
                    <tr key={b.id} className="hover:bg-gray-50 dark:bg-gray-900 dark:hover:bg-gray-800">
                      <td className="px-4 py-3 text-sm text-gray-900 dark:text-gray-100">
                        {b.title}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-800 dark:bg-gray-700 dark:text-gray-300">
                          {b.severity}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-800 dark:bg-gray-700 dark:text-gray-300">
                          {b.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* ---- Footer: Release button ---- */}
      {showReleaseButton && (
        <div className="border-t border-gray-200 pt-4 dark:border-gray-700">
          <button
            type="button"
            className="btn-primary"
            disabled={releaseDisabled}
            onClick={() => setShowReleaseDialog(true)}
            data-testid="release-version-btn"
          >
            Release Version
          </button>
          {releaseDisabled && (
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Complete all EPICs ({version.epics_done}/{version.epic_count})
              before releasing.
            </p>
          )}
        </div>
      )}

      {/* ---- Release confirmation dialog ---- */}
      {showReleaseDialog && (
        <ReleaseVersionDialog
          version={version}
          onReleased={handleReleased}
          onClose={() => setShowReleaseDialog(false)}
        />
      )}

    </section>
  );
}

export default VersionDetailPage;
