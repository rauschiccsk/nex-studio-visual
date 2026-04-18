/**
 * Project admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 Project CRUD surface against the backend REST router
 * mounted at ``/api/v1/projects`` (see ``backend/api/routes/projects.py``).
 * The page is self-contained: it owns its own local state rather than
 * reaching for the global ``projectStore`` because that store is scoped
 * to the end-user navigation (selected project, members) per DESIGN.md
 * § 3.3 — the admin CRUD surface is a distinct concern that does not
 * need to mutate the application-wide ``projectStore`` selection.  When
 * the store adds dedicated admin actions in a later feat this page can
 * switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with status + category filters, plus
 *     row-level "View", "Edit" and "Delete" actions.
 *   - ``detail`` — read-only view of a single project, including
 *     infrastructure ports, repo/source paths and audit columns.
 *   - ``create`` — form that ``POST``s a new project.  ``slug``,
 *     ``category`` and ``created_by`` are captured here because the
 *     backend schema (see ``backend/schemas/project.py``) requires them
 *     and treats them as immutable afterwards.
 *   - ``edit``   — form that ``PATCH``es only the mutable fields
 *     (``name``, ``description``, ``status``, ports, ``repo_url``,
 *     ``source_path``, ``kb_path``, ``guardian_enabled``).  ``slug``,
 *     ``category`` and ``created_by`` are rendered read-only.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 *
 * This page is distinct from ``ProjectPage`` (project overview with
 * tabs, per DESIGN.md § 3.1 at ``/projects/:slug``) and ``ProjectsPage``
 * (end-user project list at ``/projects``); it lives at
 * ``/admin/projects`` alongside the other Feat 6 CRUD surfaces.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  ProjectCategory,
  ProjectCreate,
  ProjectRead,
  ProjectStatus,
  ProjectUpdate,
} from "../types";

/** REST prefix for the Project router (see backend/main.py). */
const ENDPOINT = "/projects";

/** Page size used by the list view.  Matches the backend default. */
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
 * Port and path fields are modelled as strings because the DOM input
 * value is always a string.  They are parsed on submit into the
 * corresponding ``number | null`` / ``string | null`` API payload.
 */
interface ProjectFormState {
  name: string;
  slug: string;
  category: ProjectCategory;
  description: string;
  status: ProjectStatus;
  backend_port: string;
  frontend_port: string;
  db_port: string;
  repo_url: string;
  source_path: string;
  kb_path: string;
  guardian_enabled: boolean;
  created_by: string;
}

/** Selectable categories; mirrors the ``ProjectCategory`` literal union. */
const CATEGORY_OPTIONS: readonly ProjectCategory[] = [
  "singlemodule",
  "multimodule",
] as const;

/** Selectable statuses; mirrors the ``ProjectStatus`` literal union. */
const STATUS_OPTIONS: readonly ProjectStatus[] = [
  "active",
  "archived",
  "paused",
] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: ProjectFormState = {
  name: "",
  slug: "",
  category: "singlemodule",
  description: "",
  status: "active",
  backend_port: "",
  frontend_port: "",
  db_port: "",
  repo_url: "",
  source_path: "",
  kb_path: "",
  guardian_enabled: false,
  created_by: "",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(projectStatus: ProjectStatus): string {
  switch (projectStatus) {
    case "active":
      return "bg-emerald-100 text-emerald-800";
    case "paused":
      return "bg-amber-100 text-amber-800";
    case "archived":
      return "bg-gray-200 text-gray-700 dark:text-gray-300";
  }
}

/** Tailwind helper for category pills. */
function categoryBadgeClass(category: ProjectCategory): string {
  switch (category) {
    case "singlemodule":
      return "bg-slate-100 text-slate-800";
    case "multimodule":
      return "bg-violet-100 text-violet-800";
  }
}

/** Format an ISO timestamp as a locale date-time string, tolerant of bad input. */
function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}

/** Parse an optional port-number string into ``number | null``. */
function parseOptionalPort(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Parse an optional free-text string into ``string | null`` (empty → null). */
function parseOptionalText(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed;
}

/**
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as emitted
 * by ``uuid.UUID`` on the backend).  Rendered on the ``created_by`` input so
 * obvious typos are caught by the browser's constraint-validation API before
 * the form is submitted — the backend would otherwise reject them with a
 * generic 422 after a network round-trip.  When ``authStore`` lands and the
 * field can be auto-filled from the authenticated user, this input becomes
 * read-only and the pattern is kept as defence-in-depth.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** Render ``value ?? "—"`` for nullable detail fields. */
function renderNullable(value: string | number | null): string {
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

function ProjectAdminPage() {
  const navigate = useNavigate();

  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ProjectRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [statusFilter, setStatusFilter] = useState<ProjectStatus | "">("");
  const [categoryFilter, setCategoryFilter] = useState<ProjectCategory | "">(
    "",
  );

  const [detail, setDetail] = useState<ProjectRead | null>(null);
  const [form, setForm] = useState<ProjectFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ProjectRead>>(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          status: statusFilter || undefined,
          category: categoryFilter || undefined,
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load projects.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, statusFilter, categoryFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ProjectRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load project.";
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
        const row = await api.get<ProjectRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          name: row.name,
          slug: row.slug,
          category: row.category,
          description: row.description,
          status: row.status,
          backend_port:
            row.backend_port === null ? "" : String(row.backend_port),
          frontend_port:
            row.frontend_port === null ? "" : String(row.frontend_port),
          db_port: row.db_port === null ? "" : String(row.db_port),
          repo_url: row.repo_url ?? "",
          source_path: row.source_path ?? "",
          kb_path: row.kb_path ?? "",
          guardian_enabled: row.guardian_enabled,
          created_by: row.created_by,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load project.";
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
    navigate("/projects/new");
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
        "Delete this project? Archiving via Edit → Status = archived is usually safer. Hard delete cascades through every dependent row (members, modules, specs, tasks, bugs, delegations, migration state).",
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
        exc instanceof ApiError ? exc.message : "Failed to delete project.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: ProjectCreate = {
        name: form.name.trim(),
        slug: form.slug.trim(),
        category: form.category,
        description: form.description,
        status: form.status,
        backend_port: parseOptionalPort(form.backend_port),
        frontend_port: parseOptionalPort(form.frontend_port),
        db_port: parseOptionalPort(form.db_port),
        repo_url: parseOptionalText(form.repo_url),
        source_path: parseOptionalText(form.source_path),
        kb_path: parseOptionalText(form.kb_path),
        guardian_enabled: form.guardian_enabled,
        created_by: form.created_by.trim(),
      };
      await api.post<ProjectRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to create project.";
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
      // PATCH-style payload: only send fields the schema declares mutable.
      // ``slug``, ``category`` and ``created_by`` are immutable after
      // create (see backend/schemas/project.py) so they are excluded.
      const payload: ProjectUpdate = {
        name: form.name.trim(),
        description: form.description,
        status: form.status,
        backend_port: parseOptionalPort(form.backend_port),
        frontend_port: parseOptionalPort(form.frontend_port),
        db_port: parseOptionalPort(form.db_port),
        repo_url: parseOptionalText(form.repo_url),
        source_path: parseOptionalText(form.source_path),
        kb_path: parseOptionalText(form.kb_path),
        guardian_enabled: form.guardian_enabled,
      };
      await api.patch<ProjectRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to update project.";
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
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Projects</h2>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            System-wide project registry — lifecycle status drives which
            projects appear in the end-user navigation, and category selects
            between the single- and multi-module pipelines.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new project"
          >
            New Project
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
        <ProjectList
          items={items}
          total={total}
          isLoading={isLoading}
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
        <ProjectDetail
          project={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ProjectForm
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

interface ProjectListProps {
  items: ProjectRead[];
  total: number;
  isLoading: boolean;
  statusFilter: ProjectStatus | "";
  onStatusFilterChange: (value: ProjectStatus | "") => void;
  categoryFilter: ProjectCategory | "";
  onCategoryFilterChange: (value: ProjectCategory | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ProjectList({
  items,
  total,
  isLoading,
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
}: ProjectListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label
          htmlFor="status-filter"
          className="text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Status:
        </label>
        <select
          id="status-filter"
          value={statusFilter}
          onChange={(event) =>
            onStatusFilterChange(event.target.value as ProjectStatus | "")
          }
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
        >
          <option value="">All</option>
          {STATUS_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>

        <label
          htmlFor="category-filter"
          className="text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Category:
        </label>
        <select
          id="category-filter"
          value={categoryFilter}
          onChange={(event) =>
            onCategoryFilterChange(event.target.value as ProjectCategory | "")
          }
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
        >
          <option value="">All</option>
          {CATEGORY_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>

        <span className="ml-auto text-xs text-gray-500 dark:text-gray-400">
          {total} project{total === 1 ? "" : "s"} total
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
                Name
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Slug
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
                Guardian
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
                  Loading projects…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400"
                >
                  No projects match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50 dark:bg-gray-900 dark:hover:bg-gray-800">
                  <td className="px-4 py-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                    {item.name}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-700 dark:text-gray-300">
                    {item.slug}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${categoryBadgeClass(item.category)}`}
                    >
                      {item.category}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-700 dark:text-gray-300">
                    {item.guardian_enabled ? "enabled" : "disabled"}
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

interface ProjectDetailProps {
  project: ProjectRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ProjectDetail({
  project,
  isLoading,
  onBack,
  onEdit,
}: ProjectDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 text-sm text-gray-600 dark:text-gray-400">
        Loading project…
      </div>
    );
  }
  if (!project) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">Project not found.</p>
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
            ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900 dark:text-gray-100">
            {project.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Status
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(project.status)}`}
            >
              {project.status}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Name
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">{project.name}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Slug
          </dt>
          <dd className="font-mono text-sm text-gray-900 dark:text-gray-100">{project.slug}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Category
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${categoryBadgeClass(project.category)}`}
            >
              {project.category}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Guardian
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">
            {project.guardian_enabled ? "enabled" : "disabled"}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Description
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900 dark:text-gray-100">
            {project.description}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Backend port
          </dt>
          <dd className="font-mono text-sm text-gray-900 dark:text-gray-100">
            {renderNullable(project.backend_port)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Frontend port
          </dt>
          <dd className="font-mono text-sm text-gray-900 dark:text-gray-100">
            {renderNullable(project.frontend_port)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            DB port
          </dt>
          <dd className="font-mono text-sm text-gray-900 dark:text-gray-100">
            {renderNullable(project.db_port)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Repo URL
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {renderNullable(project.repo_url)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Source path
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {renderNullable(project.source_path)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            KB path
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {renderNullable(project.kb_path)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Created by
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {project.created_by}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Created at
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">
            {formatTimestamp(project.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Updated at
          </dt>
          <dd className="text-sm text-gray-900 dark:text-gray-100">
            {formatTimestamp(project.updated_at)}
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

interface ProjectFormProps {
  form: ProjectFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ProjectFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ProjectForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ProjectFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ProjectFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 text-sm text-gray-600 dark:text-gray-400">
        Loading project…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        {isEdit ? "Edit project" : "Create project"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label
            htmlFor="name"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Name
          </label>
          <input
            id="name"
            type="text"
            value={form.name}
            onChange={(event) => patch({ name: event.target.value })}
            required
            minLength={1}
            maxLength={255}
            placeholder="e.g. NEX Horizont"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 dark:placeholder-gray-400"
          />
        </div>

        <div>
          <label
            htmlFor="slug"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Slug
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (URL-safe, immutable after create)
            </span>
          </label>
          <input
            id="slug"
            type="text"
            value={form.slug}
            onChange={(event) => patch({ slug: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            minLength={1}
            maxLength={100}
            placeholder="e.g. nex-horizont"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500 dark:text-gray-400" : "bg-white text-gray-900 dark:text-gray-100"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="category"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Category
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (immutable after create)
            </span>
          </label>
          <select
            id="category"
            value={form.category}
            onChange={(event) =>
              patch({ category: event.target.value as ProjectCategory })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500 dark:text-gray-400" : "bg-white text-gray-900 dark:text-gray-100"
            }`}
          >
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
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
              patch({ status: event.target.value as ProjectStatus })
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
            htmlFor="description"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Description
          </label>
          <textarea
            id="description"
            value={form.description}
            onChange={(event) => patch({ description: event.target.value })}
            required
            minLength={1}
            rows={4}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 dark:placeholder-gray-400"
          />
        </div>

        <div>
          <label
            htmlFor="backend_port"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Backend port
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (from ICC Port Registry; optional)
            </span>
          </label>
          <input
            id="backend_port"
            type="number"
            inputMode="numeric"
            min={1}
            max={65535}
            value={form.backend_port}
            onChange={(event) => patch({ backend_port: event.target.value })}
            placeholder="e.g. 9176"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="frontend_port"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Frontend port
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (optional)
            </span>
          </label>
          <input
            id="frontend_port"
            type="number"
            inputMode="numeric"
            min={1}
            max={65535}
            value={form.frontend_port}
            onChange={(event) => patch({ frontend_port: event.target.value })}
            placeholder="e.g. 9177"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="db_port"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            DB port
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (optional)
            </span>
          </label>
          <input
            id="db_port"
            type="number"
            inputMode="numeric"
            min={1}
            max={65535}
            value={form.db_port}
            onChange={(event) => patch({ db_port: event.target.value })}
            placeholder="e.g. 9178"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex items-end">
          <label
            htmlFor="guardian_enabled"
            className="inline-flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            <input
              id="guardian_enabled"
              type="checkbox"
              checked={form.guardian_enabled}
              onChange={(event) =>
                patch({ guardian_enabled: event.target.checked })
              }
              className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            Guardian enabled
            <span className="text-xs font-normal text-gray-500 dark:text-gray-400">
              (runs the review pipeline for this project)
            </span>
          </label>
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="repo_url"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Repo URL
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (optional; max 255 chars)
            </span>
          </label>
          <input
            id="repo_url"
            type="text"
            value={form.repo_url}
            onChange={(event) => patch({ repo_url: event.target.value })}
            maxLength={255}
            placeholder="e.g. rauschiccsk/nex-horizont"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="source_path"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Source path
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (optional filesystem path)
            </span>
          </label>
          <input
            id="source_path"
            type="text"
            value={form.source_path}
            onChange={(event) => patch({ source_path: event.target.value })}
            placeholder="e.g. /opt/nex-horizont-src/"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="kb_path"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            KB path
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (optional knowledge-base directory)
            </span>
          </label>
          <input
            id="kb_path"
            type="text"
            value={form.kb_path}
            onChange={(event) => patch({ kb_path: event.target.value })}
            placeholder="e.g. /home/icc/knowledge/projects/nex-horizont/"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="created_by"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Created by
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (user UUID; immutable after create)
            </span>
          </label>
          <input
            id="created_by"
            type="text"
            value={form.created_by}
            onChange={(event) => patch({ created_by: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID (e.g. a31d1a12-4b5c-6d7e-8f90-123456789abc)."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500 dark:text-gray-400" : "bg-white text-gray-900 dark:text-gray-100"
            }`}
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

export default ProjectAdminPage;
