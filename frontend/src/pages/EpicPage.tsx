/**
 * Epic admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 Epic CRUD surface against the backend REST router
 * mounted at ``/api/v1/epics`` (see ``backend/api/routes/epics.py``).
 * An ``epics`` row is the top level of the Epic → Feat → Task task
 * hierarchy (DESIGN.md §1.9 Tasks hierarchy, §2 ``epics`` table) and
 * backs the end-user ``TasksPage`` / ``EpicList`` UI (DESIGN.md §3.1).
 *
 * Like the other Feat 6 admin pages (``DesignDocumentPage``,
 * ``ArchitectMessagePage``, ``ProjectModulePage``, …) this surface is
 * deliberately self-contained rather than reaching for a global
 * Zustand store: per DESIGN.md § 3.3 ``taskStore`` backs the end-user
 * ``EpicList`` / ``FeatCard`` / ``TaskItem`` browsing UI on the
 * Tasks page, which is a distinct concern from a per-row administrative
 * CRUD editor. When the store grows dedicated admin actions in a later
 * feat this page can switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``module_id`` and/or ``status``, with row-level "View", "Edit"
 *     and "Delete" actions. Note that passing a ``module_id`` filter
 *     excludes project-level epics — this mirrors the backend's
 *     indexed-column filter semantics (an epic with ``module_id IS
 *     NULL`` is a project-level epic, used by single-module projects;
 *     DESIGN.md §1.9).
 *   - ``detail`` — read-only view of a single epic: primary key,
 *     ``project_id``, ``module_id``, hierarchical ``number`` (the
 *     stable ``E<n>`` label per DESIGN.md §1.9), ``title``, ``status``
 *     and audit timestamps.
 *   - ``create`` — form that ``POST``s a new epic. ``project_id`` and
 *     ``title`` are required; ``module_id`` is optional (blank →
 *     project-level epic); ``status`` defaults to ``planned`` (DB
 *     ``server_default``). ``number`` is auto-assigned by the service
 *     layer as ``MAX(number) + 1`` per project — never sent by the
 *     client. Concurrent-create races on the same project surface as
 *     HTTP 409 (the DB-level ``UNIQUE(project_id, number)`` constraint)
 *     and are shown verbatim in the inline error banner.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``module_id``, ``title``, ``status``). ``project_id`` and
 *     ``number`` are rendered read-only — an epic belongs to exactly
 *     one project for its lifetime and its hierarchical position
 *     within the project must not be rewritten after the fact
 *     (:class:`EpicUpdate` deliberately omits both, see
 *     ``backend/schemas/epic.py``).
 *
 * ``DELETE`` is a hard delete. ``epics`` has a single inbound foreign
 * key (``feats.epic_id``) with ``ON DELETE CASCADE`` — dependent feats
 * (and the tasks under them, via ``tasks.feat_id ON DELETE CASCADE``)
 * are removed automatically at the DB level. No RESTRICT dependency
 * check is required, but the confirmation dialog warns the user that
 * the cascade can remove a substantial subtree.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / status values / constraint failures to HTTP 422 and
 * they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/epics`` alongside the other Feat 6
 * CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``,
 * ``/admin/architect-messages``, ``/admin/design-documents``). It is
 * distinct from ``TasksPage`` (the end-user ``EpicList`` /
 * ``FeatCard`` / ``TaskItem`` surface at ``/projects/:slug/tasks``,
 * DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  EpicCreate,
  EpicRead,
  EpicStatus,
  EpicUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the Epic router (see backend/main.py). */
const ENDPOINT = "/epics";

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
 * ``select`` input ``value`` is always a string. UUID inputs enforce
 * the canonical shape via the ``pattern`` attribute and the backend
 * rejects malformed values with HTTP 422. The ``status`` enum is
 * backed by an ``EpicStatus`` cast at submit time.
 */
interface EpicFormState {
  project_id: string;
  module_id: string;
  title: string;
  status: EpicStatus;
}

/**
 * Selectable statuses; mirrors the ``EpicStatus`` literal union and
 * the ``ck_epics_status`` DB CHECK constraint.
 */
const STATUS_OPTIONS: readonly EpicStatus[] = [
  "planned",
  "in_progress",
  "done",
] as const;

/** Fresh-form defaults for the create mode — ``status`` mirrors the DB ``server_default``. */
const EMPTY_FORM: EpicFormState = {
  project_id: "",
  module_id: "",
  title: "",
  status: "planned",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(value: EpicStatus): string {
  switch (value) {
    case "planned":
      return "bg-gray-100 text-gray-800";
    case "in_progress":
      return "bg-amber-100 text-amber-800";
    case "done":
      return "bg-emerald-100 text-emerald-800";
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

function EpicPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<EpicRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<EpicStatus | "">("");

  const [detail, setDetail] = useState<EpicRead | null>(null);
  const [form, setForm] = useState<EpicFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<EpicRead>>(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          project_id: projectFilter.trim() || undefined,
          module_id: moduleFilter.trim() || undefined,
          // Backend exposes the parameter as ``status`` (alias of
          // ``status_filter`` — see backend/api/routes/epics.py).
          status: statusFilter || undefined,
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load epics.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, moduleFilter, statusFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<EpicRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load epic.";
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
        const row = await api.get<EpicRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          module_id: row.module_id ?? "",
          title: row.title,
          status: row.status,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load epic.";
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
        "Delete this epic? This is a hard delete. The single inbound FK feats.epic_id is ON DELETE CASCADE — every feat under this epic, and every task under those feats (tasks.feat_id ON DELETE CASCADE), will be removed automatically at the DB level. This may discard a substantial subtree of work; proceed only if you're sure.",
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
        exc instanceof ApiError ? exc.message : "Failed to delete epic.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const moduleId = form.module_id.trim();
      const payload: EpicCreate = {
        project_id: form.project_id.trim(),
        module_id: moduleId ? moduleId : null,
        title: form.title.trim(),
        status: form.status,
      };
      await api.post<EpicRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to create epic.";
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
      // ``project_id`` and ``number`` are immutable (see
      // backend/schemas/epic.py — EpicUpdate deliberately omits them).
      // We only send the mutable fields.
      //
      // PATCH semantics on the backend: fields that are ``None`` are
      // treated as "leave unchanged" (``module_id`` is therefore sticky
      // once set). We send ``module_id`` unconditionally since the
      // service applies the update only when the value is non-``None``;
      // blank input is normalised to ``null`` here so the payload shape
      // stays consistent with the create path. Explicit "downgrade to
      // project-level" transitions are admin-only corrections and not
      // expressible through this UI — matching the backend service
      // contract.
      const moduleId = form.module_id.trim();
      const payload: EpicUpdate = {
        module_id: moduleId ? moduleId : null,
        title: form.title.trim(),
        status: form.status,
      };
      await api.patch<EpicRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to update epic.";
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
          <h2 className="text-xl font-semibold text-gray-900">Epics</h2>
          <p className="text-sm text-gray-600">
            Top level of the Epic → Feat → Task task hierarchy (DESIGN.md
            §1.9 / §2 ``epics`` table). Ordered by ``number`` ASC (E1,
            E2, …) — auto-assigned per project at create time. Delete is
            a hard delete and cascades to every feat and task underneath
            via the DB-level ``ON DELETE CASCADE`` foreign keys.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new epic"
          >
            New Epic
          </button>
        )}
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {mode.kind === "list" && (
        <EpicList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
          }}
          moduleFilter={moduleFilter}
          onModuleFilterChange={(value) => {
            setSkip(0);
            setModuleFilter(value);
          }}
          statusFilter={statusFilter}
          onStatusFilterChange={(value) => {
            setSkip(0);
            setStatusFilter(value);
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
        <EpicDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <EpicForm
          form={form}
          mode={mode.kind}
          isSaving={isSaving}
          isLoading={isLoading && mode.kind === "edit"}
          editingNumber={
            mode.kind === "edit"
              ? (items.find((item) => item.id === mode.id)?.number ?? null)
              : null
          }
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

interface EpicListProps {
  items: EpicRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  moduleFilter: string;
  onModuleFilterChange: (value: string) => void;
  statusFilter: EpicStatus | "";
  onStatusFilterChange: (value: EpicStatus | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function EpicList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  moduleFilter,
  onModuleFilterChange,
  statusFilter,
  onStatusFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: EpicListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="project-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Project ID
          </label>
          <input
            id="project-filter"
            type="text"
            value={projectFilter}
            onChange={(event) => onProjectFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show epics across all projects."
            placeholder="UUID — blank = all projects"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="module-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Module ID
          </label>
          <input
            id="module-filter"
            type="text"
            value={moduleFilter}
            onChange={(event) => onModuleFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show module-scoped epics for a specific module. Blank = include both module-scoped and project-level epics."
            placeholder="UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="status-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Status
          </label>
          <select
            id="status-filter"
            value={statusFilter}
            onChange={(event) =>
              onStatusFilterChange(event.target.value as EpicStatus | "")
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

        <span className="ml-auto text-xs text-gray-500">
          {total} epic{total === 1 ? "" : "s"} total
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                #
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Title
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Project
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Scope
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Epic ID
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Created
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {isLoading && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading epics…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No epics match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    E{item.number}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-900">
                    {item.title}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.project_id}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-700">
                    {item.module_id ? (
                      <span className="font-mono text-[11px] text-gray-700">
                        {item.module_id}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800">
                        Project-level
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.id}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.created_at)}
                  </td>
                  <td className="px-4 py-2 text-right text-sm">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        className="text-primary-700 hover:underline"
                        onClick={() => onView(item.id)}
                      >
                        View
                      </button>
                      <button
                        type="button"
                        className="text-primary-700 hover:underline"
                        onClick={() => onEdit(item.id)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="text-red-700 hover:underline"
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

      <div className="flex items-center justify-between text-sm text-gray-600">
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

interface EpicDetailProps {
  row: EpicRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function EpicDetail({ row, isLoading, onBack, onEdit }: EpicDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading epic…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Epic not found.</p>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Epic ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Number
          </dt>
          <dd className="font-mono text-sm text-gray-900">E{row.number}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Title
          </dt>
          <dd className="text-sm text-gray-900">{row.title}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
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
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Scope
          </dt>
          <dd className="text-sm text-gray-900">
            {row.module_id ? (
              <span className="font-mono text-xs">{row.module_id}</span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800">
                Project-level
              </span>
            )}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.project_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Module ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.module_id ?? "— (project-level epic)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Updated at
          </dt>
          <dd className="text-sm text-gray-900">
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

interface EpicFormProps {
  form: EpicFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  /** Stable hierarchical number of the row being edited, when known. */
  editingNumber: number | null;
  onChange: (form: EpicFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function EpicForm({
  form,
  mode,
  isSaving,
  isLoading,
  editingNumber,
  onChange,
  onCancel,
  onSubmit,
}: EpicFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<EpicFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading epic…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit epic" : "Create epic"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="project_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-500">
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
            title="Enter the project UUID this epic belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="module_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Module ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → project_modules, ON DELETE SET NULL;
              blank = project-level epic)
            </span>
          </label>
          <input
            id="module_id"
            type="text"
            value={form.module_id}
            onChange={(event) => patch({ module_id: event.target.value })}
            pattern={UUID_PATTERN}
            title="Enter the project module UUID, or leave blank for a project-level epic (used by single-module projects)."
            placeholder="blank = project-level epic"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="title"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Title
            <span className="ml-1 text-xs font-normal text-gray-500">
              (1–500 chars, required)
            </span>
          </label>
          <input
            id="title"
            type="text"
            value={form.title}
            onChange={(event) => patch({ title: event.target.value })}
            required
            minLength={1}
            maxLength={500}
            placeholder="e.g. User authentication & session lifecycle"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="status"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Status
            <span className="ml-1 text-xs font-normal text-gray-500">
              (planned | in_progress | done; defaults to planned)
            </span>
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({ status: event.target.value as EpicStatus })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        {isEdit && editingNumber !== null && (
          <div>
            <label
              htmlFor="number"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Number
              <span className="ml-1 text-xs font-normal text-gray-500">
                (auto-assigned per project; immutable)
              </span>
            </label>
            <input
              id="number"
              type="text"
              value={`E${editingNumber}`}
              readOnly
              className="block w-full rounded-md border border-gray-300 bg-gray-100 px-3 py-2 font-mono text-sm text-gray-500 shadow-sm"
            />
          </div>
        )}
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

export default EpicPage;
