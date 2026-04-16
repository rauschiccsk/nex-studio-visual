/**
 * ArchitectSession admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ArchitectSession CRUD surface against the backend
 * REST router mounted at ``/api/v1/architect-sessions`` (see
 * ``backend/api/routes/architect_sessions.py``). An
 * ``architect_sessions`` row is a chat session scoped to either a
 * project (``module_id`` is ``null`` → Foundation / project-level
 * session, DESIGN.md §1.5 "NULL = Foundation/project session") or a
 * specific project module. See DESIGN.md §1.11 ArchitectSession and
 * §1.5 ``architect_sessions`` table.
 *
 * Like the other Feat 6 admin pages (``ProjectModulePage``,
 * ``ProjectMemberPage``, ``MigrationIdMapPage``, …) this surface is
 * deliberately self-contained rather than reaching for the global
 * ``architectStore``: per DESIGN.md § 3.3 that store backs the
 * end-user ``ArchitectPage`` chat UI (sessions, messages, streaming
 * state), which is a distinct concern from a per-row administrative
 * CRUD editor. When the store grows dedicated admin actions in a later
 * feat this page can switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``module_id``, ``status`` and/or ``created_by``, with row-level
 *     "View", "Edit" and "Delete" actions.
 *   - ``detail`` — read-only view of a single session: primary key,
 *     ``project_id``, ``module_id``, ``status``, ``created_by``,
 *     ``closed_at`` and audit timestamps.
 *   - ``create`` — form that ``POST``s a new session. ``project_id``
 *     and ``created_by`` are required; ``status`` defaults to
 *     ``active`` (Pydantic / DB ``server_default``); ``module_id`` is
 *     optional (empty → project-level session); ``closed_at`` is
 *     optional and normally left blank on create.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``module_id``, ``status``, ``closed_at``). ``project_id`` and
 *     ``created_by`` are rendered read-only — a session belongs to
 *     exactly one project and one creator for its lifetime
 *     (:class:`ArchitectSessionUpdate` deliberately omits them, see
 *     ``backend/schemas/architect_session.py``).
 *
 * ``DELETE`` is a hard delete — the single inbound FK
 * (``architect_messages.session_id``) uses ``ON DELETE CASCADE`` so
 * dependent messages are removed automatically at the DB level. The
 * confirmation dialog warns the user; ``PATCH status='closed'`` is the
 * preferred soft-close path when the conversation history must be
 * preserved.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / status values / constraint failures to HTTP 422 and
 * they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/architect-sessions`` alongside the
 * other Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``). It is distinct from ``ArchitectPage``
 * (the end-user chat surface at ``/projects/:slug/architect`` and
 * ``/projects/:slug/modules/:code/architect``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  ArchitectSessionCreate,
  ArchitectSessionRead,
  ArchitectSessionStatus,
  ArchitectSessionUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the ArchitectSession router (see backend/main.py). */
const ENDPOINT = "/architect-sessions";

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
 * ``select`` / ``datetime-local`` input ``value`` is always a string.
 * UUID inputs enforce the canonical shape via the ``pattern`` attribute
 * and the backend rejects malformed values with HTTP 422. The
 * ``status`` enum is backed by an ``ArchitectSessionStatus`` cast at
 * submit time.
 */
interface ArchitectSessionFormState {
  project_id: string;
  module_id: string;
  status: ArchitectSessionStatus;
  created_by: string;
  closed_at: string;
}

/**
 * Selectable statuses; mirrors the ``ArchitectSessionStatus`` literal
 * union and the ``ck_architect_sessions_status`` DB CHECK constraint.
 */
const STATUS_OPTIONS: readonly ArchitectSessionStatus[] = [
  "active",
  "closed",
] as const;

/** Fresh-form defaults for the create mode — ``status`` mirrors the DB ``server_default``. */
const EMPTY_FORM: ArchitectSessionFormState = {
  project_id: "",
  module_id: "",
  status: "active",
  created_by: "",
  closed_at: "",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(value: ArchitectSessionStatus): string {
  switch (value) {
    case "active":
      return "bg-emerald-100 text-emerald-800";
    case "closed":
      return "bg-gray-100 text-gray-800";
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
 * Convert a ``datetime-local`` input value (``YYYY-MM-DDTHH:mm``) into
 * the ISO-8601 timestamp expected by the backend. ``datetime-local``
 * strings are interpreted as local time by ``new Date(...)`` which is
 * the desired behaviour — the user types a wall-clock time and the
 * resulting ``Date.toISOString()`` normalises it to UTC for
 * transmission. Returns ``null`` for blank input or for values that
 * cannot be parsed (the latter shouldn't happen because the input
 * itself enforces the format).
 */
function parseOptionalDateTime(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const parsed = new Date(trimmed);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/**
 * Convert an ISO-8601 timestamp from the backend into the
 * ``YYYY-MM-DDTHH:mm`` shape required by the ``datetime-local`` input.
 * Returns an empty string when ``iso`` is ``null`` or unparseable so
 * the input renders blank instead of throwing. Local components are
 * rebuilt by hand to avoid the UTC off-by-one issue you hit if you
 * slice ``parsed.toISOString()``.
 */
function isoToDateTimeLocal(iso: string | null): string {
  if (!iso) {
    return "";
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(
    parsed.getDate(),
  )}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
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

function ArchitectSessionPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ArchitectSessionRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<
    ArchitectSessionStatus | ""
  >("");
  const [createdByFilter, setCreatedByFilter] = useState("");

  const [detail, setDetail] = useState<ArchitectSessionRead | null>(null);
  const [form, setForm] = useState<ArchitectSessionFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ArchitectSessionRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            module_id: moduleFilter.trim() || undefined,
            status: statusFilter || undefined,
            created_by: createdByFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load Architect sessions.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, moduleFilter, statusFilter, createdByFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ArchitectSessionRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load Architect session.";
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
        const row = await api.get<ArchitectSessionRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          module_id: row.module_id ?? "",
          status: row.status,
          created_by: row.created_by,
          closed_at: isoToDateTimeLocal(row.closed_at),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load Architect session.";
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
        "Delete this Architect session? Dependent messages (architect_messages) will be removed automatically (ON DELETE CASCADE). Prefer PATCH status='closed' to preserve conversation history.",
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
          : "Failed to delete Architect session.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const moduleId = form.module_id.trim();
      const payload: ArchitectSessionCreate = {
        project_id: form.project_id.trim(),
        module_id: moduleId ? moduleId : null,
        status: form.status,
        created_by: form.created_by.trim(),
        closed_at: parseOptionalDateTime(form.closed_at),
      };
      await api.post<ArchitectSessionRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create Architect session.";
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
      // ``project_id`` and ``created_by`` are immutable (see
      // backend/schemas/architect_session.py — ArchitectSessionUpdate
      // deliberately omits them). We only send the mutable fields;
      // ``module_id`` and ``closed_at`` are normalised from blank input
      // to ``null`` so the columns clear cleanly (project-level scope /
      // re-open a closed session).
      const moduleId = form.module_id.trim();
      const payload: ArchitectSessionUpdate = {
        module_id: moduleId ? moduleId : null,
        status: form.status,
        closed_at: parseOptionalDateTime(form.closed_at),
      };
      await api.patch<ArchitectSessionRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update Architect session.";
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
          <h2 className="text-xl font-semibold text-gray-900">
            Architect sessions
          </h2>
          <p className="text-sm text-gray-600">
            Architect chat sessions scoped to a project (``module_id ==
            null`` → Foundation / project-level session) or a specific
            project module (DESIGN.md §1.11 / §1.5). Deleting a session
            cascades to its messages — prefer ``status = 'closed'`` for a
            soft-close that preserves conversation history.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new Architect session"
          >
            New Session
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
        <ArchitectSessionList
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
          createdByFilter={createdByFilter}
          onCreatedByFilterChange={(value) => {
            setSkip(0);
            setCreatedByFilter(value);
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
        <ArchitectSessionDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ArchitectSessionForm
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

interface ArchitectSessionListProps {
  items: ArchitectSessionRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  moduleFilter: string;
  onModuleFilterChange: (value: string) => void;
  statusFilter: ArchitectSessionStatus | "";
  onStatusFilterChange: (value: ArchitectSessionStatus | "") => void;
  createdByFilter: string;
  onCreatedByFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ArchitectSessionList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  moduleFilter,
  onModuleFilterChange,
  statusFilter,
  onStatusFilterChange,
  createdByFilter,
  onCreatedByFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ArchitectSessionListProps) {
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
            title="Enter a canonical UUID, or leave blank to show sessions across all projects."
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
            title="Enter a canonical UUID, or leave blank to include both module-level and project-level sessions."
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
              onStatusFilterChange(
                event.target.value as ArchitectSessionStatus | "",
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
            htmlFor="created-by-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Created by
          </label>
          <input
            id="created-by-filter"
            type="text"
            value={createdByFilter}
            onChange={(event) => onCreatedByFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show sessions from all creators."
            placeholder="User UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} session{total === 1 ? "" : "s"} total
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Session
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
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Created by
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Closed at
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
                  Loading Architect sessions…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No Architect sessions match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-900">
                    {item.id}
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
                        project-level
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.created_by}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.closed_at)}
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

interface ArchitectSessionDetailProps {
  row: ArchitectSessionRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ArchitectSessionDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ArchitectSessionDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading Architect session…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Architect session not found.</p>
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
            Session ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
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
            {row.module_id ?? "— (project-level session)"}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created by
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.created_by}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Closed at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.closed_at)}
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

interface ArchitectSessionFormProps {
  form: ArchitectSessionFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ArchitectSessionFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ArchitectSessionForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ArchitectSessionFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ArchitectSessionFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading Architect session…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit Architect session" : "Create Architect session"}
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
            title="Enter the project UUID this session is scoped to."
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
              blank = project-level / Foundation session)
            </span>
          </label>
          <input
            id="module_id"
            type="text"
            value={form.module_id}
            onChange={(event) => patch({ module_id: event.target.value })}
            pattern={UUID_PATTERN}
            title="Enter the project module UUID, or leave blank for a project-level session."
            placeholder="blank = project-level session"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="created_by"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Created by
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → users; immutable after create)
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
            title="Enter the UUID of the user opening the session."
            placeholder="e.g. 9f0c8f9e-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="status"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Status
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({
                status: event.target.value as ArchitectSessionStatus,
              })
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

        <div>
          <label
            htmlFor="closed_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Closed at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; stamped automatically by the service when status
              transitions to closed)
            </span>
          </label>
          <input
            id="closed_at"
            type="datetime-local"
            value={form.closed_at}
            onChange={(event) => patch({ closed_at: event.target.value })}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
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

export default ArchitectSessionPage;
