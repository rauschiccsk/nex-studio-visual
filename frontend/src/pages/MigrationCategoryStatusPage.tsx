/**
 * MigrationCategoryStatus admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 MigrationCategoryStatus CRUD surface against the
 * backend REST router mounted at
 * ``/api/v1/migration-category-statuses`` (see
 * ``backend/api/routes/migration_category_statuses.py``). Whereas
 * ``MigrationBatchPage`` (Task 6.6) records the audit trail of individual
 * extract/load runs, this page surfaces the single per-category lifecycle
 * row ((``project_id``, ``category``) is uniquely constrained — one row
 * per category per project). Operators use it to see at a glance whether
 * a category is ``pending``, ``in_progress``, ``completed`` or
 * ``failed``, when it last ran and any manual notes about encoding
 * issues or data-quality caveats.
 *
 * Like ``MigrationBatchPage`` this admin surface is deliberately
 * self-contained rather than reaching for ``migrationStore``: per
 * DESIGN.md § 3.3 that store is scoped to the end-user migration control
 * panel at ``/projects/:slug/migration``, which is a distinct concern
 * from an administrative CRUD editor. When ``migrationStore`` later
 * grows admin actions this page can swap over without changing its
 * visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with project-id, category and
 *     status filters, plus row-level "View", "Edit" and "Delete"
 *     actions.
 *   - ``detail`` — read-only view of a single row with the full status,
 *     ``last_run_at`` timestamp, notes and audit timestamps.
 *   - ``create`` — form that ``POST``s a new status row.
 *     ``project_id`` and ``category`` are captured here because the
 *     backend schema treats them as the immutable row identity (see
 *     ``backend/schemas/migration_category_status.py``). ``id``,
 *     ``created_at`` and ``updated_at`` are server-generated and
 *     intentionally absent from the form.
 *   - ``edit``   — form that ``PATCH``es only the mutable fields
 *     (``status``, ``last_run_at``, ``notes``). ``project_id`` and
 *     ``category`` are rendered read-only because they form the
 *     immutable ``(project, category)`` identity pair.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 *
 * This page sits under ``/admin/migration-category-statuses`` alongside
 * the other Feat 6 CRUD surfaces (``/admin/users``,
 * ``/admin/projects``, ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  MigrationCategoryStatusCreate,
  MigrationCategoryStatusRead,
  MigrationCategoryStatusStatus,
  MigrationCategoryStatusUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the MigrationCategoryStatus router (see backend/main.py). */
const ENDPOINT = "/migration-category-statuses";

/** Page size used by the list view. Matches the backend default. */
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
 * ``last_run_at`` is modelled as a string because the DOM
 * ``datetime-local`` input emits the ``YYYY-MM-DDTHH:mm`` shape. It is
 * parsed into an ISO-8601 timestamp on submit.
 */
interface MigrationCategoryStatusFormState {
  project_id: string;
  category: string;
  status: MigrationCategoryStatusStatus;
  last_run_at: string;
  notes: string;
}

/** Selectable statuses; mirrors the ``MigrationCategoryStatusStatus`` literal union. */
const STATUS_OPTIONS: readonly MigrationCategoryStatusStatus[] = [
  "pending",
  "in_progress",
  "completed",
  "failed",
] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: MigrationCategoryStatusFormState = {
  project_id: "",
  category: "",
  status: "pending",
  last_run_at: "",
  notes: "",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(
  categoryStatus: MigrationCategoryStatusStatus,
): string {
  switch (categoryStatus) {
    case "pending":
      return "bg-sky-100 text-sky-800";
    case "in_progress":
      return "bg-amber-100 text-amber-800";
    case "completed":
      return "bg-emerald-100 text-emerald-800";
    case "failed":
      return "bg-red-100 text-red-800";
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
 * emitted by ``uuid.UUID`` on the backend). Rendered on the
 * ``project_id`` inputs so obvious typos are caught by the browser's
 * constraint-validation API before the form is submitted — the backend
 * would otherwise reject them with a generic 422 after a network
 * round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

function MigrationCategoryStatusPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<MigrationCategoryStatusRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<
    MigrationCategoryStatusStatus | ""
  >("");

  const [detail, setDetail] = useState<MigrationCategoryStatusRead | null>(
    null,
  );
  const [form, setForm] =
    useState<MigrationCategoryStatusFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<
        PaginatedResponse<MigrationCategoryStatusRead>
      >(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          project_id: projectFilter.trim() || undefined,
          category: categoryFilter.trim() || undefined,
          status: statusFilter || undefined,
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load migration category statuses.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, categoryFilter, statusFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<MigrationCategoryStatusRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load migration category status.";
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
        const row = await api.get<MigrationCategoryStatusRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          category: row.category,
          status: row.status,
          last_run_at: isoToDateTimeLocal(row.last_run_at),
          notes: row.notes ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load migration category status.";
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
        "Delete this migration category status? Setting Status = failed via Edit is the preferred soft-disable path. migration_category_status has no inbound foreign keys, so a hard delete only drops the lifecycle row — it does not touch migration_batch or migration_id_map.",
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
          : "Failed to delete migration category status.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: MigrationCategoryStatusCreate = {
        project_id: form.project_id.trim(),
        category: form.category.trim(),
        status: form.status,
        last_run_at: parseOptionalDateTime(form.last_run_at),
        notes: parseOptionalText(form.notes),
      };
      await api.post<MigrationCategoryStatusRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create migration category status.";
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
      // ``project_id`` and ``category`` are immutable after create (see
      // backend/schemas/migration_category_status.py — the row identity
      // pair must not be rewritten) so they are excluded.
      const payload: MigrationCategoryStatusUpdate = {
        status: form.status,
        last_run_at: parseOptionalDateTime(form.last_run_at),
        notes: parseOptionalText(form.notes),
      };
      await api.patch<MigrationCategoryStatusRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update migration category status.";
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
            Migration category statuses
          </h2>
          <p className="text-sm text-gray-600">
            Per-project lifecycle row — one status per category per project
            (pending → in_progress → completed | failed). The pair
            (project, category) forms the immutable identity.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new migration category status"
          >
            New Category Status
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
        <MigrationCategoryStatusList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
          }}
          categoryFilter={categoryFilter}
          onCategoryFilterChange={(value) => {
            setSkip(0);
            setCategoryFilter(value);
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
        <MigrationCategoryStatusDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <MigrationCategoryStatusForm
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

interface MigrationCategoryStatusListProps {
  items: MigrationCategoryStatusRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  categoryFilter: string;
  onCategoryFilterChange: (value: string) => void;
  statusFilter: MigrationCategoryStatusStatus | "";
  onStatusFilterChange: (value: MigrationCategoryStatusStatus | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function MigrationCategoryStatusList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  categoryFilter,
  onCategoryFilterChange,
  statusFilter,
  onStatusFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: MigrationCategoryStatusListProps) {
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
            title="Enter a canonical UUID, or leave blank to show statuses across all projects."
            placeholder="UUID — blank = all projects"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="category-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Category
          </label>
          <input
            id="category-filter"
            type="text"
            value={categoryFilter}
            onChange={(event) => onCategoryFilterChange(event.target.value)}
            maxLength={20}
            placeholder="e.g. PAB"
            className="w-40 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
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
                event.target.value as MigrationCategoryStatusStatus | "",
              )
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

        <span className="ml-auto text-xs text-gray-500">
          {total} category status{total === 1 ? "" : "es"} total
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
                Category
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
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Last run
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Updated
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
                  colSpan={6}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading migration category statuses…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No migration category statuses match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs uppercase text-gray-700">
                    {item.category}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.project_id}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.last_run_at)}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.updated_at)}
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

interface MigrationCategoryStatusDetailProps {
  row: MigrationCategoryStatusRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function MigrationCategoryStatusDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: MigrationCategoryStatusDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading migration category status…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">
          Migration category status not found.
        </p>
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
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.project_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Category
          </dt>
          <dd className="font-mono text-sm uppercase text-gray-900">
            {row.category}
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
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Last run at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.last_run_at)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Notes
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900">
            {row.notes || "—"}
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

interface MigrationCategoryStatusFormProps {
  form: MigrationCategoryStatusFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: MigrationCategoryStatusFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function MigrationCategoryStatusForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: MigrationCategoryStatusFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<MigrationCategoryStatusFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading migration category status…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit
          ? "Edit migration category status"
          : "Create migration category status"}
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

        <div>
          <label
            htmlFor="category"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Category
            <span className="ml-1 text-xs font-normal text-gray-500">
              (max 20 chars; immutable after create)
            </span>
          </label>
          <input
            id="category"
            type="text"
            value={form.category}
            onChange={(event) => patch({ category: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            minLength={1}
            maxLength={20}
            placeholder="e.g. PAB"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm uppercase shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
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
                status: event.target.value as MigrationCategoryStatusStatus,
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

        <div className="sm:col-span-2">
          <label
            htmlFor="last_run_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Last run at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; usually set when a batch completes for this category)
            </span>
          </label>
          <input
            id="last_run_at"
            type="datetime-local"
            value={form.last_run_at}
            onChange={(event) => patch({ last_run_at: event.target.value })}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="notes"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Notes
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; encoding issues, data-quality caveats, etc.)
            </span>
          </label>
          <textarea
            id="notes"
            value={form.notes}
            onChange={(event) => patch({ notes: event.target.value })}
            rows={5}
            placeholder="e.g. Source has Windows-1250 encoding; 3 records failed on invalid date '0000-00-00'."
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

export default MigrationCategoryStatusPage;
