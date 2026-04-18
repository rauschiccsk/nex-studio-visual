/**
 * ProjectModule admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ProjectModule CRUD surface against the backend REST
 * router mounted at ``/api/v1/project-modules`` (see
 * ``backend/api/routes/project_modules.py``). A ``project_modules`` row
 * is the per-module record for a multi-module project — e.g. NEX
 * Horizont's ``PAB``, ``GSC``, ``MIG`` (DESIGN.md §1.5 ProjectModule,
 * §2.2 ``project_modules`` table, D-10 NEX Horizont module seeding).
 *
 * Like the other Feat 6 admin pages (``MigrationIdMapPage``,
 * ``GuardianPrecedentPage``, …) this surface is
 * deliberately self-contained rather than reaching for the global
 * ``moduleStore``: per DESIGN.md § 3.3 that store backs the end-user
 * Module Registry page / dependency-graph UI, which is a distinct
 * concern from a per-row administrative CRUD editor. When the store
 * adds dedicated admin actions in a later feat this page can switch
 * over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``status`` and/or ``category``, with row-level "View", "Edit"
 *     and "Delete" actions.
 *   - ``detail`` — read-only view of a single module including the
 *     ``(project_id, code)`` natural key, ``name``, ``category``,
 *     ``status``, the DESIGN.md path and audit timestamps.
 *   - ``create`` — form that ``POST``s a new module. ``project_id``,
 *     ``code``, ``name`` and ``category`` are required; ``status``
 *     defaults to ``planned`` (the DB ``server_default``) and
 *     ``design_doc_path`` is optional. ``id``, ``created_at`` and
 *     ``updated_at`` are server-generated and intentionally absent
 *     from the form.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``code``, ``name``, ``category``, ``status``,
 *     ``design_doc_path``). ``project_id`` is rendered read-only —
 *     a module belongs to exactly one project for its lifetime and
 *     is deleted rather than reassigned
 *     (:class:`ProjectModuleUpdate` deliberately omits it, see
 *     ``backend/schemas/project_module.py``).
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps duplicate
 * ``(project_id, code)`` pairs to HTTP 409 and invalid ``status`` /
 * constraint failures to HTTP 422; both are shown verbatim in the
 * inline error banner.
 *
 * This page sits under ``/admin/project-modules`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``). It is
 * distinct from ``ModuleRegistryPage`` (the end-user Module Registry
 * visualisation at ``/projects/:slug/modules``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  ProjectModuleCreate,
  ProjectModuleRead,
  ProjectModuleStatus,
  ProjectModuleUpdate,
} from "../types";

/** REST prefix for the ProjectModule router (see backend/main.py). */
const ENDPOINT = "/project-modules";

/** Page size used by the list view. Matches the backend default (capped at 100). */
const PAGE_SIZE = 20;

/** Finite mode state keeps the render logic explicit and linter-friendly. */
type Mode =
  | { kind: "list" }
  | { kind: "detail"; id: string }
  | { kind: "create" }
  | { kind: "edit"; id: string };

/**
 * Shape of the mutable fields in the create / edit forms.
 *
 * All values are captured as plain strings because the DOM ``text`` /
 * ``select`` input ``value`` is always a string; the canonical UUID
 * format on ``project_id`` is enforced via the ``pattern`` attribute
 * and by the backend on submit, and the ``status`` enum is backed by
 * a ``ProjectModuleStatus`` cast at submit time.
 */
interface ProjectModuleFormState {
  project_id: string;
  code: string;
  name: string;
  category: string;
  status: ProjectModuleStatus;
  design_doc_path: string;
}

/**
 * Selectable statuses; mirrors the ``ProjectModuleStatus`` literal
 * union and the ``ck_project_modules_status`` DB CHECK constraint.
 */
const STATUS_OPTIONS: readonly ProjectModuleStatus[] = [
  "planned",
  "in_design",
  "in_development",
  "done",
] as const;

/** Fresh-form defaults for the create mode — ``status`` mirrors the DB ``server_default``. */
const EMPTY_FORM: ProjectModuleFormState = {
  project_id: "",
  code: "",
  name: "",
  category: "",
  status: "planned",
  design_doc_path: "",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(value: ProjectModuleStatus): string {
  switch (value) {
    case "planned":
      return "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300";
    case "in_design":
      return "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300";
    case "in_development":
      return "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300";
    case "done":
      return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300";
  }
}

/** Format an ISO timestamp as a locale date-time string, tolerant of bad input. */
function formatTimestamp(iso: string | null): string {
  if (!iso) {
    return "—";
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}

/**
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend). Rendered on UUID inputs so
 * obvious typos are caught by the browser's constraint-validation API
 * before the form is submitted — the backend would otherwise reject
 * them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

function ProjectModulePage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ProjectModuleRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<ProjectModuleStatus | "">(
    "",
  );
  const [categoryFilter, setCategoryFilter] = useState("");

  const [detail, setDetail] = useState<ProjectModuleRead | null>(null);
  const [form, setForm] = useState<ProjectModuleFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ProjectModuleRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            status: statusFilter || undefined,
            category: categoryFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load project modules.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, statusFilter, categoryFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ProjectModuleRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load project module.";
      setError(message);
      setDetail(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // -------------------------------------------------------------- effects
  useEffect(() => {
    if (mode.kind === "list") {
      void loadList();
    }
  }, [mode, loadList]);

  useEffect(() => {
    if (mode.kind === "detail") {
      void loadDetail(mode.id);
    }
  }, [mode, loadDetail]);

  useEffect(() => {
    // Seed the edit form with the current row whenever edit mode opens.
    if (mode.kind !== "edit") {
      return;
    }
    let cancelled = false;
    (async () => {
      setIsLoading(true);
      setError(null);
      try {
        const row = await api.get<ProjectModuleRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          code: row.code,
          name: row.name,
          category: row.category,
          status: row.status,
          design_doc_path: row.design_doc_path ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load project module.";
        setError(message);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mode]);

  // ------------------------------------------------------------- handlers
  const openList = () => {
    setDetail(null);
    setForm(EMPTY_FORM);
    setError(null);
    setMode({ kind: "list" });
  };

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setError(null);
    setMode({ kind: "create" });
  };

  const openDetail = (id: string) => {
    setError(null);
    setMode({ kind: "detail", id });
  };

  const openEdit = (id: string) => {
    setError(null);
    setMode({ kind: "edit", id });
  };

  const handleDelete = async (id: string) => {
    if (
      !window.confirm(
        "Delete this project module? Inbound foreign keys either cascade (module_dependencies) or are nulled out (specifications, kb_documents, tasks, architect_sessions) — the module itself is removed permanently.",
      )
    ) {
      return;
    }
    setError(null);
    try {
      await api.delete(`${ENDPOINT}/${id}`);
      await loadList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to delete project module.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const designDocPath = form.design_doc_path.trim();
      const payload: ProjectModuleCreate = {
        project_id: form.project_id.trim(),
        code: form.code.trim(),
        name: form.name.trim(),
        category: form.category.trim(),
        status: form.status,
        design_doc_path: designDocPath ? designDocPath : null,
      };
      await api.post<ProjectModuleRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create project module.";
      setError(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleUpdate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode.kind !== "edit") {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      // ``project_id`` is immutable (see backend/schemas/project_module.py —
      // ProjectModuleUpdate deliberately omits it). We only send the
      // mutable fields; ``design_doc_path`` is normalised from blank
      // input to ``null`` so the column clears cleanly.
      const designDocPath = form.design_doc_path.trim();
      const payload: ProjectModuleUpdate = {
        code: form.code.trim(),
        name: form.name.trim(),
        category: form.category.trim(),
        status: form.status,
        design_doc_path: designDocPath ? designDocPath : null,
      };
      await api.patch<ProjectModuleRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update project module.";
      setError(message);
    } finally {
      setIsSaving(false);
    }
  };

  // ---------------------------------------------------------- derived data
  const totalPages = useMemo(() => {
    if (total === 0) {
      return 1;
    }
    return Math.max(1, Math.ceil(total / PAGE_SIZE));
  }, [total]);
  const currentPage = Math.floor(skip / PAGE_SIZE) + 1;

  // ---------------------------------------------------------------- render
  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
            Project modules
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Per-module records for multi-module projects (DESIGN.md §1.5
            / §2.2). The ``(project_id, code)`` pair is unique per
            project; a module belongs to exactly one project for its
            lifetime and is deleted rather than reassigned.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new project module"
          >
            New Module
          </button>
        )}
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-900/30 p-3 text-sm text-red-800 dark:text-red-300"
        >
          {error}
        </div>
      )}

      {mode.kind === "list" && (
        <ProjectModuleList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
          }}
          statusFilter={statusFilter}
          onStatusFilterChange={(value) => {
            setSkip(0);
            setStatusFilter(value);
          }}
          categoryFilter={categoryFilter}
          onCategoryFilterChange={(value) => {
            setSkip(0);
            setCategoryFilter(value);
          }}
          currentPage={currentPage}
          totalPages={totalPages}
          onPreviousPage={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
          onNextPage={() => {
            if (skip + PAGE_SIZE < total) {
              setSkip(skip + PAGE_SIZE);
            }
          }}
          onView={openDetail}
          onEdit={openEdit}
          onDelete={handleDelete}
        />
      )}

      {mode.kind === "detail" && (
        <ProjectModuleDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ProjectModuleForm
          form={form}
          mode={mode.kind}
          isSaving={isSaving}
          isLoading={isLoading && mode.kind === "edit"}
          onChange={setForm}
          onCancel={openList}
          onSubmit={mode.kind === "create" ? handleCreate : handleUpdate}
        />
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*                              Sub-components                                */
/* -------------------------------------------------------------------------- */

interface ProjectModuleListProps {
  items: ProjectModuleRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  statusFilter: ProjectModuleStatus | "";
  onStatusFilterChange: (value: ProjectModuleStatus | "") => void;
  categoryFilter: string;
  onCategoryFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ProjectModuleList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  statusFilter,
  onStatusFilterChange,
  categoryFilter,
  onCategoryFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ProjectModuleListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="project-filter"
            className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Project ID
          </label>
          <input
            id="project-filter"
            type="text"
            value={projectFilter}
            onChange={(event) => onProjectFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show modules across all projects."
            placeholder="UUID — blank = all projects"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="status-filter"
            className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Status
          </label>
          <select
            id="status-filter"
            value={statusFilter}
            onChange={(event) =>
              onStatusFilterChange(
                event.target.value as ProjectModuleStatus | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="category-filter"
            className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Category
          </label>
          <input
            id="category-filter"
            type="text"
            value={categoryFilter}
            onChange={(event) => onCategoryFilterChange(event.target.value)}
            placeholder="e.g. Katalógy (blank = all)"
            maxLength={50}
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500 dark:text-gray-400">
          {total} module{total === 1 ? "" : "s"} total
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-900">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Code
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Name
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Category
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Project
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Created
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {isLoading && (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400"
                >
                  Loading project modules…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400"
                >
                  No project modules match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50 dark:bg-gray-900 dark:hover:bg-gray-800">
                  <td className="px-4 py-2 font-mono text-xs font-semibold text-gray-900 dark:text-gray-100">
                    {item.code}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-900 dark:text-gray-100">
                    {item.name}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-700 dark:text-gray-300">
                    {item.category}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500 dark:text-gray-400">
                    {item.project_id}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500 dark:text-gray-400">
                    {formatTimestamp(item.created_at)}
                  </td>
                  <td className="px-4 py-2 text-right text-sm">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        className="text-primary-700 hover:underline dark:text-primary-400"
                        onClick={() => onView(item.id)}
                      >
                        View
                      </button>
                      <button
                        type="button"
                        className="text-primary-700 hover:underline dark:text-primary-400"
                        onClick={() => onEdit(item.id)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="text-red-700 hover:underline dark:text-red-400"
                        onClick={() => onDelete(item.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-600 dark:text-gray-400">
        <span>
          Page {currentPage} of {totalPages}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn-secondary"
            onClick={onPreviousPage}
            disabled={currentPage <= 1 || isLoading}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onNextPage}
            disabled={currentPage >= totalPages || isLoading}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

interface ProjectModuleDetailProps {
  row: ProjectModuleRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ProjectModuleDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ProjectModuleDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 text-sm text-gray-600 dark:text-gray-400">
        Loading project module…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">Project module not found.</p>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 shadow-sm">
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Module ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900 dark:text-gray-100">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Status
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(row.status)}`}
            >
              {row.status}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Code
          </dt>
          <dd className="font-mono text-sm font-semibold text-gray-900 dark:text-gray-100">
            {row.code}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Category
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">{row.category}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Name
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">{row.name}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {row.project_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            DESIGN.md path
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {row.design_doc_path ?? "— (not yet generated)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Created at
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">
            {formatTimestamp(row.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Updated at
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">
            {formatTimestamp(row.updated_at)}
          </dd>
        </div>
      </dl>

      <div className="flex gap-2 pt-2">
        <button type="button" className="btn-primary" onClick={onEdit}>
          Edit
        </button>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    </div>
  );
}

interface ProjectModuleFormProps {
  form: ProjectModuleFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ProjectModuleFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ProjectModuleForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ProjectModuleFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ProjectModuleFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 text-sm text-gray-600 dark:text-gray-400">
        Loading project module…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        {isEdit ? "Edit project module" : "Create project module"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="project_id"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (UUID; FK → projects, ON DELETE CASCADE; immutable after
              create)
            </span>
          </label>
          <input
            id="project_id"
            type="text"
            value={form.project_id}
            onChange={(event) => patch({ project_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the project UUID this module belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500 dark:text-gray-400" : "bg-white text-gray-900 dark:text-gray-100"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="code"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Code
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (≤10 chars; unique per project)
            </span>
          </label>
          <input
            id="code"
            type="text"
            value={form.code}
            onChange={(event) => patch({ code: event.target.value })}
            required
            minLength={1}
            maxLength={10}
            placeholder="e.g. PAB"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="category"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Category
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (≤50 chars)
            </span>
          </label>
          <input
            id="category"
            type="text"
            value={form.category}
            onChange={(event) => patch({ category: event.target.value })}
            required
            minLength={1}
            maxLength={50}
            placeholder="e.g. Katalógy"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 dark:placeholder-gray-400"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="name"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Name
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (≤255 chars)
            </span>
          </label>
          <input
            id="name"
            type="text"
            value={form.name}
            onChange={(event) => patch({ name: event.target.value })}
            required
            minLength={1}
            maxLength={255}
            placeholder="e.g. Katalóg partnerov"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 dark:placeholder-gray-400"
          />
        </div>

        <div>
          <label
            htmlFor="status"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Status
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({ status: event.target.value as ProjectModuleStatus })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 dark:placeholder-gray-400"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="design_doc_path"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            DESIGN.md path
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (optional — absolute filesystem path to the module
              DESIGN.md in the KB)
            </span>
          </label>
          <input
            id="design_doc_path"
            type="text"
            value={form.design_doc_path}
            onChange={(event) =>
              patch({ design_doc_path: event.target.value })
            }
            placeholder="e.g. /home/icc/knowledge/projects/nex-horizont/modules/pab/DESIGN.md"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={onCancel}
          disabled={isSaving}
        >
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={isSaving}>
          {isSaving ? "Saving…" : isEdit ? "Save changes" : "Create"}
        </button>
      </div>
    </form>
  );
}

export default ProjectModulePage;
