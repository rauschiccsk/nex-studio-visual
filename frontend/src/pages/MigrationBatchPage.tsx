/**
 * MigrationBatch admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 MigrationBatch CRUD surface against the backend REST
 * router mounted at ``/api/v1/migration-batches`` (see
 * ``backend/api/routes/migration_batches.py``). The page is
 * self-contained: it owns its own local state rather than reaching for
 * the global ``migrationStore`` because that store (per DESIGN.md § 3.3)
 * is scoped to the end-user migration control panel exposed at
 * ``/projects/:slug/migration`` (per-category status and run-batch
 * orchestration). The admin CRUD surface is a distinct concern that
 * does not need to mutate the application-wide migration counters and
 * status grid. When ``migrationStore`` adds dedicated admin actions in
 * a later feat this page can switch over without changing its visible
 * surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with project-id, category, direction
 *     and status filters, plus row-level "View", "Edit" and "Delete"
 *     actions.
 *   - ``detail`` — read-only view of a single migration batch,
 *     including source/target/error counts, error log excerpt and the
 *     start / complete / created timestamps.
 *   - ``create`` — form that ``POST``s a new migration batch.
 *     ``project_id``, ``category`` and ``direction`` are captured here
 *     because the backend schema (see
 *     ``backend/schemas/migration_batch.py``) requires them and treats
 *     them as immutable afterwards. The batch ``id`` and ``created_at``
 *     are server-generated and intentionally absent from the form.
 *   - ``edit``   — form that ``PATCH``es only the mutable fields
 *     (``status``, ``source_count``, ``target_count``, ``error_count``,
 *     ``error_log``, ``started_at``, ``completed_at``).
 *     ``project_id``, ``category`` and ``direction`` are rendered
 *     read-only because they form the immutable batch identity (see
 *     the schema docstring).
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 *
 * This page sits under ``/admin/migration-batches`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  MigrationBatchCreate,
  MigrationBatchDirection,
  MigrationBatchRead,
  MigrationBatchStatus,
  MigrationBatchUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the MigrationBatch router (see backend/main.py). */
const ENDPOINT = "/migration-batches";

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
 * ``source_count``, ``target_count`` and ``error_count`` are modelled
 * as strings because the DOM ``number`` input always exposes ``value``
 * as a string. They are parsed on submit into the ``number | null``
 * payload expected by the backend.
 *
 * ``started_at`` and ``completed_at`` are likewise strings because the
 * DOM ``datetime-local`` input emits the ``YYYY-MM-DDTHH:mm`` shape.
 * They are parsed into ISO-8601 timestamps on submit.
 */
interface MigrationBatchFormState {
  project_id: string;
  category: string;
  direction: MigrationBatchDirection;
  status: MigrationBatchStatus;
  source_count: string;
  target_count: string;
  error_count: string;
  error_log: string;
  started_at: string;
  completed_at: string;
}

/** Selectable directions; mirrors the ``MigrationBatchDirection`` literal union. */
const DIRECTION_OPTIONS: readonly MigrationBatchDirection[] = [
  "extract",
  "load",
] as const;

/** Selectable statuses; mirrors the ``MigrationBatchStatus`` literal union. */
const STATUS_OPTIONS: readonly MigrationBatchStatus[] = [
  "pending",
  "running",
  "completed",
  "failed",
] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: MigrationBatchFormState = {
  project_id: "",
  category: "",
  direction: "extract",
  status: "pending",
  source_count: "",
  target_count: "",
  error_count: "0",
  error_log: "",
  started_at: "",
  completed_at: "",
};

/** Tailwind helper for direction pills. */
function directionBadgeClass(direction: MigrationBatchDirection): string {
  switch (direction) {
    case "extract":
      return "bg-indigo-100 text-indigo-800";
    case "load":
      return "bg-teal-100 text-teal-800";
  }
}

/** Tailwind helper for status pills. */
function statusBadgeClass(batchStatus: MigrationBatchStatus): string {
  switch (batchStatus) {
    case "pending":
      return "bg-sky-100 text-sky-800";
    case "running":
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
 * Parse a ``number`` input value into ``number | null``. Non-numeric or
 * blank input yields ``null``; the backend treats the count fields as
 * nullable. Decimal input is truncated to an integer because all three
 * count columns are integer columns at the DB layer.
 */
function parseOptionalInt(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isNaN(parsed) ? null : parsed;
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

/** Render ``value ?? "—"`` for nullable detail fields. */
function renderNullable(value: string | number | null): string {
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

function MigrationBatchPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<MigrationBatchRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [directionFilter, setDirectionFilter] = useState<
    MigrationBatchDirection | ""
  >("");
  const [statusFilter, setStatusFilter] = useState<MigrationBatchStatus | "">(
    "",
  );

  const [detail, setDetail] = useState<MigrationBatchRead | null>(null);
  const [form, setForm] = useState<MigrationBatchFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<MigrationBatchRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            category: categoryFilter.trim() || undefined,
            direction: directionFilter || undefined,
            status: statusFilter || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load migration batches.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, categoryFilter, directionFilter, statusFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<MigrationBatchRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load migration batch.";
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
        const row = await api.get<MigrationBatchRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          category: row.category,
          direction: row.direction,
          status: row.status,
          source_count:
            row.source_count === null ? "" : String(row.source_count),
          target_count:
            row.target_count === null ? "" : String(row.target_count),
          error_count:
            row.error_count === null ? "" : String(row.error_count),
          error_log: row.error_log ?? "",
          started_at: isoToDateTimeLocal(row.started_at),
          completed_at: isoToDateTimeLocal(row.completed_at),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load migration batch.";
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
        "Delete this migration batch? Setting Status = failed via Edit is the preferred soft-disable path. Hard delete nulls out migration_id_map.batch_id (ON DELETE SET NULL) so cross-reference integrity of migrated rows is preserved.",
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
          : "Failed to delete migration batch.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: MigrationBatchCreate = {
        project_id: form.project_id.trim(),
        category: form.category.trim(),
        direction: form.direction,
        status: form.status,
        source_count: parseOptionalInt(form.source_count),
        target_count: parseOptionalInt(form.target_count),
        error_count: parseOptionalInt(form.error_count),
        error_log: parseOptionalText(form.error_log),
        started_at: parseOptionalDateTime(form.started_at),
        completed_at: parseOptionalDateTime(form.completed_at),
      };
      await api.post<MigrationBatchRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create migration batch.";
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
      // ``project_id``, ``category`` and ``direction`` are immutable
      // after create (see backend/schemas/migration_batch.py — the
      // batch identity triple must not be rewritten) so they are
      // excluded.
      const payload: MigrationBatchUpdate = {
        status: form.status,
        source_count: parseOptionalInt(form.source_count),
        target_count: parseOptionalInt(form.target_count),
        error_count: parseOptionalInt(form.error_count),
        error_log: parseOptionalText(form.error_log),
        started_at: parseOptionalDateTime(form.started_at),
        completed_at: parseOptionalDateTime(form.completed_at),
      };
      await api.patch<MigrationBatchRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update migration batch.";
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
            Migration batches
          </h2>
          <p className="text-sm text-gray-600">
            Per-project extract / load run record — one row per migration
            invocation, append-only (no ``updated_at``). The triple
            (project, category, direction) forms the immutable batch
            identity.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new migration batch"
          >
            New Migration Batch
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
        <MigrationBatchList
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
          directionFilter={directionFilter}
          onDirectionFilterChange={(value) => {
            setSkip(0);
            setDirectionFilter(value);
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
        <MigrationBatchDetail
          batch={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <MigrationBatchForm
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

interface MigrationBatchListProps {
  items: MigrationBatchRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  categoryFilter: string;
  onCategoryFilterChange: (value: string) => void;
  directionFilter: MigrationBatchDirection | "";
  onDirectionFilterChange: (value: MigrationBatchDirection | "") => void;
  statusFilter: MigrationBatchStatus | "";
  onStatusFilterChange: (value: MigrationBatchStatus | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function MigrationBatchList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  categoryFilter,
  onCategoryFilterChange,
  directionFilter,
  onDirectionFilterChange,
  statusFilter,
  onStatusFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: MigrationBatchListProps) {
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
            title="Enter a canonical UUID, or leave blank to show batches across all projects."
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
            maxLength={10}
            placeholder="e.g. PAB"
            className="w-32 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="direction-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Direction
          </label>
          <select
            id="direction-filter"
            value={directionFilter}
            onChange={(event) =>
              onDirectionFilterChange(
                event.target.value as MigrationBatchDirection | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            {DIRECTION_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
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
                event.target.value as MigrationBatchStatus | "",
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
          {total} migration batch{total === 1 ? "" : "es"} total
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
                Direction
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
                Target
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Errors
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Started
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Completed
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
                  colSpan={9}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading migration batches…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No migration batches match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs uppercase text-gray-700">
                    {item.category}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${directionBadgeClass(item.direction)}`}
                    >
                      {item.direction}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-700">
                    {item.source_count ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-700">
                    {item.target_count ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-700">
                    {item.error_count ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.started_at)}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.completed_at)}
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

interface MigrationBatchDetailProps {
  batch: MigrationBatchRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function MigrationBatchDetail({
  batch,
  isLoading,
  onBack,
  onEdit,
}: MigrationBatchDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading migration batch…
      </div>
    );
  }
  if (!batch) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Migration batch not found.</p>
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
            {batch.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {batch.project_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Category
          </dt>
          <dd className="font-mono text-sm uppercase text-gray-900">
            {batch.category}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Direction
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${directionBadgeClass(batch.direction)}`}
            >
              {batch.direction}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Status
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(batch.status)}`}
            >
              {batch.status}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Source count
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(batch.source_count)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Target count
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(batch.target_count)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Error count
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(batch.error_count)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Error log
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900">
            {batch.error_log || "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Started at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(batch.started_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Completed at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(batch.completed_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(batch.created_at)}
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

interface MigrationBatchFormProps {
  form: MigrationBatchFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: MigrationBatchFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function MigrationBatchForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: MigrationBatchFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<MigrationBatchFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading migration batch…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit migration batch" : "Create migration batch"}
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
              (max 10 chars; immutable after create)
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
            maxLength={10}
            placeholder="e.g. PAB"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm uppercase shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="direction"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Direction
            <span className="ml-1 text-xs font-normal text-gray-500">
              (immutable after create)
            </span>
          </label>
          <select
            id="direction"
            value={form.direction}
            onChange={(event) =>
              patch({
                direction: event.target.value as MigrationBatchDirection,
              })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          >
            {DIRECTION_OPTIONS.map((option) => (
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
              patch({ status: event.target.value as MigrationBatchStatus })
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
            htmlFor="source_count"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Source count
            <span className="ml-1 text-xs font-normal text-gray-500">
              (Btrieve records)
            </span>
          </label>
          <input
            id="source_count"
            type="number"
            min={0}
            step={1}
            value={form.source_count}
            onChange={(event) => patch({ source_count: event.target.value })}
            placeholder="e.g. 1024"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="target_count"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Target count
            <span className="ml-1 text-xs font-normal text-gray-500">
              (PostgreSQL rows loaded)
            </span>
          </label>
          <input
            id="target_count"
            type="number"
            min={0}
            step={1}
            value={form.target_count}
            onChange={(event) => patch({ target_count: event.target.value })}
            placeholder="e.g. 1020"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="error_count"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Error count
            <span className="ml-1 text-xs font-normal text-gray-500">
              (server default 0)
            </span>
          </label>
          <input
            id="error_count"
            type="number"
            min={0}
            step={1}
            value={form.error_count}
            onChange={(event) => patch({ error_count: event.target.value })}
            placeholder="0"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="started_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Started at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional)
            </span>
          </label>
          <input
            id="started_at"
            type="datetime-local"
            value={form.started_at}
            onChange={(event) => patch({ started_at: event.target.value })}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="completed_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Completed at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; usually filled when status flips to completed/failed)
            </span>
          </label>
          <input
            id="completed_at"
            type="datetime-local"
            value={form.completed_at}
            onChange={(event) => patch({ completed_at: event.target.value })}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="error_log"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Error log
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; first N errors, truncated)
            </span>
          </label>
          <textarea
            id="error_log"
            value={form.error_log}
            onChange={(event) => patch({ error_log: event.target.value })}
            rows={5}
            placeholder="e.g. row 42: invalid date '0000-00-00'; row 87: FK customer_id=999 not found"
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

export default MigrationBatchPage;
