/**
 * Bug admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 Bug CRUD surface against the backend REST router
 * mounted at ``/api/v1/bugs`` (see ``backend/api/routes/bugs.py``).
 * The page is self-contained: it owns its own local state rather than
 * reaching for the global ``bugStore`` because that store (per
 * DESIGN.md § 3.3) is scoped to the end-user bug workflow (open /
 * resolved counts surfaced inside the project area).  The admin CRUD
 * surface is a distinct concern that does not need to mutate the
 * application-wide bug counters.  When ``bugStore`` adds dedicated
 * admin actions in a later feat this page can switch over without
 * changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with project-id, status, severity
 *     and source filters, plus row-level "View", "Edit" and "Delete"
 *     actions.
 *   - ``detail`` — read-only view of a single bug, including the
 *     reporter, environment, commit hash and audit columns.
 *   - ``create`` — form that ``POST``s a new bug.  ``project_id`` and
 *     ``created_by`` are captured here because the backend schema
 *     (see ``backend/schemas/bug.py``) requires them and treats them
 *     as immutable afterwards.  ``bug_number`` is auto-assigned by
 *     the service layer as ``MAX(bug_number) + 1`` per project, so it
 *     is intentionally absent from the form.
 *   - ``edit``   — form that ``PATCH``es only the mutable fields
 *     (``title``, ``description``, ``severity``, ``status``,
 *     ``source``, ``reported_by``, ``environment``, ``resolved_at``,
 *     ``commit_hash``).  ``project_id``, ``bug_number`` and
 *     ``created_by`` are rendered read-only.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 *
 * This page sits under ``/admin/bugs`` alongside the other Feat 6
 * CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/guardian-precedents``).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  BugCreate,
  BugRead,
  BugSeverity,
  BugSource,
  BugStatus,
  BugUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the Bug router (see backend/main.py). */
const ENDPOINT = "/bugs";

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
 * ``resolved_at`` is modelled as a string because the DOM
 * ``datetime-local`` input always yields a string.  It is parsed on
 * submit into the ``string | null`` ISO-8601 payload expected by the
 * backend.
 */
interface BugFormState {
  project_id: string;
  title: string;
  description: string;
  severity: BugSeverity;
  status: BugStatus;
  source: BugSource;
  reported_by: string;
  environment: string;
  resolved_at: string;
  commit_hash: string;
  created_by: string;
}

/** Selectable severities; mirrors the ``BugSeverity`` literal union. */
const SEVERITY_OPTIONS: readonly BugSeverity[] = [
  "critical",
  "major",
  "minor",
] as const;

/** Selectable statuses; mirrors the ``BugStatus`` literal union. */
const STATUS_OPTIONS: readonly BugStatus[] = [
  "new",
  "accepted",
  "in_progress",
  "resolved",
  "wont_fix",
] as const;

/** Selectable sources; mirrors the ``BugSource`` literal union. */
const SOURCE_OPTIONS: readonly BugSource[] = ["internal", "customer"] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: BugFormState = {
  project_id: "",
  title: "",
  description: "",
  severity: "minor",
  status: "new",
  source: "internal",
  reported_by: "",
  environment: "",
  resolved_at: "",
  commit_hash: "",
  created_by: "",
};

/** Tailwind helper for severity pills. */
function severityBadgeClass(severity: BugSeverity): string {
  switch (severity) {
    case "critical":
      return "bg-red-100 text-red-800";
    case "major":
      return "bg-orange-100 text-orange-800";
    case "minor":
      return "bg-yellow-100 text-yellow-800";
  }
}

/** Tailwind helper for status pills. */
function statusBadgeClass(bugStatus: BugStatus): string {
  switch (bugStatus) {
    case "new":
      return "bg-sky-100 text-sky-800";
    case "accepted":
      return "bg-indigo-100 text-indigo-800";
    case "in_progress":
      return "bg-amber-100 text-amber-800";
    case "resolved":
      return "bg-emerald-100 text-emerald-800";
    case "wont_fix":
      return "bg-gray-200 text-gray-700";
  }
}

/** Tailwind helper for source pills. */
function sourceBadgeClass(source: BugSource): string {
  switch (source) {
    case "internal":
      return "bg-slate-100 text-slate-800";
    case "customer":
      return "bg-violet-100 text-violet-800";
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

/** Parse an optional free-text string into ``string | null`` (empty → null). */
function parseOptionalText(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed;
}

/**
 * Convert a ``datetime-local`` input value (``YYYY-MM-DDTHH:mm``) into the
 * ISO-8601 timestamp expected by the backend.  ``datetime-local`` strings
 * are interpreted as local time by ``new Date(...)`` which is the desired
 * behaviour — the user types a wall-clock time and the resulting
 * ``Date.toISOString()`` normalises it to UTC for transmission.  Returns
 * ``null`` for blank input or for values that cannot be parsed (the
 * latter shouldn't happen because the input itself enforces the format).
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
 * Returns an empty string when ``iso`` is ``null`` or unparseable so the
 * input renders blank instead of throwing.
 */
function isoToDateTimeLocal(iso: string | null): string {
  if (!iso) {
    return "";
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  // Strip the timezone suffix and seconds — the input only accepts
  // ``YYYY-MM-DDTHH:mm`` (or with ``:ss`` if ``step`` is set).  We
  // rebuild the local-time components by hand to avoid the UTC
  // off-by-one issue you hit if you slice ``parsed.toISOString()``.
  const pad = (n: number) => n.toString().padStart(2, "0");
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(
      parsed.getDate(),
    )}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  );
}

/**
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend).  Rendered on the
 * ``project_id`` and ``created_by`` inputs so obvious typos are caught
 * by the browser's constraint-validation API before the form is
 * submitted — the backend would otherwise reject them with a generic
 * 422 after a network round-trip.  When ``authStore`` lands and the
 * ``created_by`` field can be auto-filled from the authenticated user,
 * that input becomes read-only and the pattern is kept as
 * defence-in-depth.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** Render ``value ?? "—"`` for nullable detail fields. */
function renderNullable(value: string | number | null): string {
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

function BugPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<BugRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<BugStatus | "">("");
  const [severityFilter, setSeverityFilter] = useState<BugSeverity | "">("");
  const [sourceFilter, setSourceFilter] = useState<BugSource | "">("");

  const [detail, setDetail] = useState<BugRead | null>(null);
  const [form, setForm] = useState<BugFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<BugRead>>(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          project_id: projectFilter.trim() || undefined,
          status: statusFilter || undefined,
          severity: severityFilter || undefined,
          source: sourceFilter || undefined,
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load bugs.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, statusFilter, severityFilter, sourceFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<BugRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load bug.";
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
        const row = await api.get<BugRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          title: row.title,
          description: row.description,
          severity: row.severity,
          status: row.status,
          source: row.source,
          reported_by: row.reported_by ?? "",
          environment: row.environment ?? "",
          resolved_at: isoToDateTimeLocal(row.resolved_at),
          commit_hash: row.commit_hash ?? "",
          created_by: row.created_by,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load bug.";
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
        "Delete this bug? Setting Status = wont_fix via Edit is the preferred soft-disable path. Hard delete cascades through dependent bug-fix tasks.",
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
        exc instanceof ApiError ? exc.message : "Failed to delete bug.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: BugCreate = {
        project_id: form.project_id.trim(),
        title: form.title.trim(),
        description: form.description,
        severity: form.severity,
        status: form.status,
        source: form.source,
        reported_by: parseOptionalText(form.reported_by),
        environment: parseOptionalText(form.environment),
        resolved_at: parseOptionalDateTime(form.resolved_at),
        commit_hash: parseOptionalText(form.commit_hash),
        created_by: form.created_by.trim(),
      };
      await api.post<BugRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to create bug.";
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
      // ``project_id``, ``bug_number`` and ``created_by`` are immutable
      // after create (see backend/schemas/bug.py) so they are excluded.
      const payload: BugUpdate = {
        title: form.title.trim(),
        description: form.description,
        severity: form.severity,
        status: form.status,
        source: form.source,
        reported_by: parseOptionalText(form.reported_by),
        environment: parseOptionalText(form.environment),
        resolved_at: parseOptionalDateTime(form.resolved_at),
        commit_hash: parseOptionalText(form.commit_hash),
      };
      await api.patch<BugRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to update bug.";
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
          <h2 className="text-xl font-semibold text-gray-900">Bugs</h2>
          <p className="text-sm text-gray-600">
            Project-scoped bug registry — severity drives triage, status
            drives lifecycle, and source distinguishes internal QA from
            customer reports. ``bug_number`` is auto-assigned per project.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new bug"
          >
            New Bug
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
        <BugList
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
          severityFilter={severityFilter}
          onSeverityFilterChange={(value) => {
            setSkip(0);
            setSeverityFilter(value);
          }}
          sourceFilter={sourceFilter}
          onSourceFilterChange={(value) => {
            setSkip(0);
            setSourceFilter(value);
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
        <BugDetail
          bug={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <BugForm
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

interface BugListProps {
  items: BugRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  statusFilter: BugStatus | "";
  onStatusFilterChange: (value: BugStatus | "") => void;
  severityFilter: BugSeverity | "";
  onSeverityFilterChange: (value: BugSeverity | "") => void;
  sourceFilter: BugSource | "";
  onSourceFilterChange: (value: BugSource | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function BugList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  statusFilter,
  onStatusFilterChange,
  severityFilter,
  onSeverityFilterChange,
  sourceFilter,
  onSourceFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: BugListProps) {
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
            title="Enter a canonical UUID, or leave blank to show bugs across all projects."
            placeholder="UUID — blank = all projects"
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
              onStatusFilterChange(event.target.value as BugStatus | "")
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
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
            htmlFor="severity-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Severity
          </label>
          <select
            id="severity-filter"
            value={severityFilter}
            onChange={(event) =>
              onSeverityFilterChange(event.target.value as BugSeverity | "")
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            {SEVERITY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="source-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Source
          </label>
          <select
            id="source-filter"
            value={sourceFilter}
            onChange={(event) =>
              onSourceFilterChange(event.target.value as BugSource | "")
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            {SOURCE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} bug{total === 1 ? "" : "s"} total
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
                Severity
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
                Source
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Environment
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
                  Loading bugs…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No bugs match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs text-gray-700">
                    #{item.bug_number}
                  </td>
                  <td className="px-4 py-2 text-sm font-medium text-gray-900">
                    {item.title}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${severityBadgeClass(item.severity)}`}
                    >
                      {item.severity}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${sourceBadgeClass(item.source)}`}
                    >
                      {item.source}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-700">
                    {item.environment ?? "—"}
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

interface BugDetailProps {
  bug: BugRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function BugDetail({ bug, isLoading, onBack, onEdit }: BugDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading bug…
      </div>
    );
  }
  if (!bug) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Bug not found.</p>
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
            ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {bug.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Bug number
          </dt>
          <dd className="font-mono text-sm text-gray-900">#{bug.bug_number}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Title
          </dt>
          <dd className="text-sm text-gray-900">{bug.title}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Severity
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${severityBadgeClass(bug.severity)}`}
            >
              {bug.severity}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Status
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(bug.status)}`}
            >
              {bug.status}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Source
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${sourceBadgeClass(bug.source)}`}
            >
              {bug.source}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Environment
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(bug.environment)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Description
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900">
            {bug.description}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Reported by
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(bug.reported_by)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Commit hash
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {renderNullable(bug.commit_hash)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {bug.project_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created by
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {bug.created_by}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Resolved at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(bug.resolved_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(bug.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Updated at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(bug.updated_at)}
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

interface BugFormProps {
  form: BugFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: BugFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function BugForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: BugFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<BugFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading bug…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit bug" : "Create bug"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="project_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; immutable after create)
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
            title="Enter a canonical UUID (e.g. a31d1a12-4b5c-6d7e-8f90-123456789abc)."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="title"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Title
          </label>
          <input
            id="title"
            type="text"
            value={form.title}
            onChange={(event) => patch({ title: event.target.value })}
            required
            minLength={1}
            maxLength={500}
            placeholder="e.g. Login fails for users with non-ASCII passwords"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="description"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Description
            <span className="ml-1 text-xs font-normal text-gray-500">
              (steps to reproduce, expected vs actual)
            </span>
          </label>
          <textarea
            id="description"
            value={form.description}
            onChange={(event) => patch({ description: event.target.value })}
            required
            minLength={1}
            rows={5}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="severity"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Severity
          </label>
          <select
            id="severity"
            value={form.severity}
            onChange={(event) =>
              patch({ severity: event.target.value as BugSeverity })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {SEVERITY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
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
              patch({ status: event.target.value as BugStatus })
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
            htmlFor="source"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Source
          </label>
          <select
            id="source"
            value={form.source}
            onChange={(event) =>
              patch({ source: event.target.value as BugSource })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {SOURCE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="environment"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Environment
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; max 50 chars)
            </span>
          </label>
          <input
            id="environment"
            type="text"
            value={form.environment}
            onChange={(event) => patch({ environment: event.target.value })}
            maxLength={50}
            placeholder="e.g. production"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="reported_by"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Reported by
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; customer or internal name)
            </span>
          </label>
          <input
            id="reported_by"
            type="text"
            value={form.reported_by}
            onChange={(event) => patch({ reported_by: event.target.value })}
            maxLength={255}
            placeholder="e.g. dominik"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="resolved_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Resolved at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; usually set automatically when status → resolved)
            </span>
          </label>
          <input
            id="resolved_at"
            type="datetime-local"
            value={form.resolved_at}
            onChange={(event) => patch({ resolved_at: event.target.value })}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="commit_hash"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Commit hash
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; max 40 chars)
            </span>
          </label>
          <input
            id="commit_hash"
            type="text"
            value={form.commit_hash}
            onChange={(event) => patch({ commit_hash: event.target.value })}
            maxLength={40}
            placeholder="e.g. 5e56c213f…"
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
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
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

export default BugPage;
