/**
 * VersionsPage — version list for a project.
 *
 * Route: ``projects/:slug/versions`` (child of ProjectLayout)
 *
 * Receives the resolved project from ``ProjectLayout`` via outlet context —
 * no extra projects-list fetch needed.  Version numbers follow the ICC
 * convention: projects start at v0.1 and v1.0 is the first
 * production/deploy release.
 *
 * ENTER key in the "Nová verzia" dialog moves focus to the next field
 * instead of submitting the form, preventing accidental early submission.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { Plus, AlertCircle, Tag } from "lucide-react";

import VersionProgressBar from "../components/versions/VersionProgressBar";
import VersionStatusBadge from "../components/versions/VersionStatusBadge";
import { ApiError } from "../services/api";
import {
  createVersion,
  listVersions,
  releaseVersion,
} from "../services/api/versions";
import type { ProjectLayoutContext } from "./ProjectPage";
import type { Version, VersionCreate } from "../types/version";
import { getUserRole } from "../utils/auth";
import { formatDate } from "../utils/format";

// ── Create Version Dialog ────────────────────────────────────────────────────

interface CreateVersionDialogProps {
  projectId: string;
  onClose: () => void;
  onCreated: (v: Version) => void;
}

function CreateVersionDialog({
  projectId,
  onClose,
  onCreated,
}: CreateVersionDialogProps) {
  const [form, setForm]       = useState<VersionCreate>({
    version_number: "",
    name: "",
    description: "",
    target_date: "",
  });
  const [isSaving, setIsSaving] = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setIsSaving(true);
      setError(null);
      try {
        const payload: VersionCreate = {
          version_number: form.version_number,
        };
        if (form.name)        payload.name        = form.name;
        if (form.description) payload.description = form.description;
        if (form.target_date) payload.target_date = form.target_date;

        const created = await createVersion(projectId, payload);
        onCreated(created);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Neočakávaná chyba.");
      } finally {
        setIsSaving(false);
      }
    },
    [form, projectId, onCreated],
  );

  /**
   * Prevent ENTER from submitting the form when focus is on a text/date
   * input.  Instead, move focus to the next focusable element so the user
   * can fill in all fields before explicitly clicking "Vytvoriť".
   * ENTER inside a <textarea> is intentionally left alone (newline insertion).
   */
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLFormElement>) => {
      if (e.key !== "Enter" || !(e.target instanceof HTMLInputElement)) return;
      e.preventDefault();
      const form = e.currentTarget;
      const focusables = Array.from(
        form.querySelectorAll<HTMLElement>(
          "input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled])",
        ),
      );
      const idx  = focusables.indexOf(e.target as HTMLElement);
      const next = focusables[idx + 1];
      if (next !== undefined) {
        next.focus();
      }
    },
    [],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-xl border border-gray-700 bg-gray-800 p-6 shadow-2xl">
        <h3 className="mb-5 text-lg font-semibold text-gray-100">
          Nová verzia
        </h3>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} onKeyDown={handleKeyDown} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-300">
              Číslo verzie <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              required
              value={form.version_number}
              onChange={(e) =>
                setForm((f) => ({ ...f, version_number: e.target.value }))
              }
              className="w-full rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="napr. 0.1"
              autoFocus
            />
            <p className="mt-1 text-xs text-gray-500">
              Projekty začínajú od v0.1. Prvá produkčná verzia je v1.0.
            </p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-300">
              Názov
            </label>
            <input
              type="text"
              value={form.name ?? ""}
              onChange={(e) =>
                setForm((f) => ({ ...f, name: e.target.value }))
              }
              className="w-full rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="napr. MVP základ"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-300">
              Popis
            </label>
            <textarea
              value={form.description ?? ""}
              onChange={(e) =>
                setForm((f) => ({ ...f, description: e.target.value }))
              }
              rows={3}
              className="w-full rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-300">
              Cieľový dátum
            </label>
            <input
              type="date"
              value={form.target_date ?? ""}
              onChange={(e) =>
                setForm((f) => ({ ...f, target_date: e.target.value }))
              }
              className="w-full rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isSaving}
              className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 disabled:opacity-50"
            >
              Zrušiť
            </button>
            <button
              type="submit"
              disabled={isSaving}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
            >
              {isSaving ? "Vytváram…" : "Vytvoriť"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

function VersionsPage() {
  const { slug }         = useParams<{ slug: string }>();
  const { project }      = useOutletContext<ProjectLayoutContext>();

  const [versions,   setVersions]   = useState<Version[]>([]);
  const [isLoading,  setIsLoading]  = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [releasing,  setReleasing]  = useState<string | null>(null);

  const role = getUserRole();
  const isRi = role === "ri";

  // ── Load versions ─────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const versionList = await listVersions(project.id);
      setVersions(versionList);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Nepodarilo sa načítať verzie.",
      );
    } finally {
      setIsLoading(false);
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // ── Release handler ───────────────────────────────────────────────────────

  const handleRelease = useCallback(async (id: string) => {
    if (!window.confirm("Uvoľniť túto verziu? Akcia je nevratná.")) return;
    setReleasing(id);
    try {
      const updated = await releaseVersion(id);
      setVersions((prev) => prev.map((v) => (v.id === id ? updated : v)));
    } catch (err) {
      window.alert(
        err instanceof ApiError ? err.message : "Uvoľnenie zlyhalo.",
      );
    } finally {
      setReleasing(null);
    }
  }, []);

  // ── Create handler ────────────────────────────────────────────────────────

  const handleCreated = useCallback((v: Version) => {
    setVersions((prev) => [v, ...prev]);
    setShowCreate(false);
  }, []);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <section className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-100">Verzie</h2>
          {!isLoading && !error && (
            <p className="mt-0.5 text-sm text-gray-500">
              {versions.length === 0
                ? "Žiadne verzie"
                : `${versions.length} ${versions.length === 1 ? "verzia" : "verzií"}`}
            </p>
          )}
        </div>
        {isRi && !isLoading && !error && (
          <button
            type="button"
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-gray-900"
            onClick={() => setShowCreate(true)}
            data-testid="new-version-btn"
          >
            <Plus className="h-4 w-4" />
            Nová verzia
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-16 text-gray-500 text-sm">
          Načítavam verzie…
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && versions.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-xl border border-gray-700 bg-gray-800/50 py-20 text-center">
          <Tag className="mb-3 h-10 w-10 text-gray-600" />
          <p className="text-sm font-medium text-gray-400">Žiadne verzie</p>
          <p className="mt-1 text-xs text-gray-600">
            Vytvor prvú verziu — odporúčame začať od v0.1
          </p>
        </div>
      )}

      {/* Versions table */}
      {!isLoading && !error && versions.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-gray-700">
          <table className="min-w-full divide-y divide-gray-700">
            <thead className="bg-gray-800/60">
              <tr>
                {[
                  "Verzia",
                  "Názov",
                  "Stav",
                  "Cieľový dátum",
                  "Priebeh",
                  ...(isRi ? ["Akcie"] : []),
                ].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700 bg-gray-800">
              {versions.map((v) => (
                <tr key={v.id} className="hover:bg-gray-750 transition-colors">
                  <td className="whitespace-nowrap px-4 py-3 text-sm font-medium">
                    <Link
                      to={`/projects/${slug}/versions/${v.id}`}
                      className="text-primary hover:underline"
                      data-testid={`version-link-${v.id}`}
                    >
                      {v.version_number}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-400">
                    {v.name ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <VersionStatusBadge status={v.status} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-400">
                    {formatDate(v.target_date)}
                  </td>
                  <td className="px-4 py-3 text-sm min-w-[140px]">
                    <VersionProgressBar
                      epicsDone={v.epics_done}
                      epicCount={v.epic_count}
                    />
                  </td>
                  {isRi && (
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <div className="flex gap-2">
                        {v.status !== "released" && (
                          <button
                            className="rounded-md border border-primary/40 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
                            data-testid={`release-btn-${v.id}`}
                            disabled={releasing === v.id}
                            onClick={() => void handleRelease(v.id)}
                          >
                            {releasing === v.id ? "Uvoľňujem…" : "Uvoľniť"}
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create dialog */}
      {showCreate && (
        <CreateVersionDialog
          projectId={project.id}
          onClose={() => setShowCreate(false)}
          onCreated={handleCreated}
        />
      )}
    </section>
  );
}

export default VersionsPage;
