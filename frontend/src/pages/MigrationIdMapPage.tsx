/**
 * MigrationIdMap admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 MigrationIdMap CRUD surface against the backend REST
 * router mounted at ``/api/v1/migration-id-maps`` (see
 * ``backend/api/routes/migration_id_maps.py``). ``migration_id_map`` is
 * the legacy Btrieve → PostgreSQL key crosswalk consumed by the
 * Migration module: one row per ``(project_id, category, source_key)``
 * pointing at a new ``target_id`` (stringified UUID, not an FK to any
 * specific table). Operators use this surface to audit, correct or
 * replay individual key mappings when a re-run relocates a record's new
 * UUID, when a source key was mis-typed, or when a batch is re-attached
 * to a different run.
 *
 * Like the other Feat 6 admin pages (``MigrationBatchPage``,
 * ``MigrationCategoryStatusPage``) this surface is deliberately
 * self-contained rather than reaching for ``migrationStore``: per
 * DESIGN.md § 3.3 that store is scoped to the end-user migration control
 * panel at ``/projects/:slug/migration`` (per-category progress grid
 * and batch orchestration), which is a distinct concern from a
 * per-row administrative CRUD editor. When ``migrationStore`` later
 * grows admin actions this page can swap over without changing its
 * visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with project-id, category,
 *     source-key and batch-id filters, plus row-level "View", "Edit"
 *     and "Delete" actions.
 *   - ``detail`` — read-only view of a single mapping row with the
 *     full natural key (project, category, source_key), the current
 *     ``target_id``, the originating ``batch_id`` and audit
 *     timestamps.
 *   - ``create`` — form that ``POST``s a new mapping row.
 *     ``project_id``, ``category`` and ``source_key`` are captured
 *     here because the backend schema treats the triple as the
 *     immutable natural key (see
 *     ``backend/schemas/migration_id_map.py`` — the
 *     ``uq_migration_id_map_project_category_source_key`` UNIQUE
 *     constraint). ``id``, ``created_at`` and ``updated_at`` are
 *     server-generated and intentionally absent from the form.
 *   - ``edit``   — form that ``PATCH``es only the mutable fields
 *     (``target_id``, ``batch_id``). ``project_id``, ``category`` and
 *     ``source_key`` are rendered read-only because the natural key
 *     must not be rewritten after the fact.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 *
 * This page sits under ``/admin/migration-id-maps`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  MigrationIdMapCreate,
  MigrationIdMapRead,
  MigrationIdMapUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the MigrationIdMap router (see backend/main.py). */
const ENDPOINT = "/migration-id-maps";

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
 * ``batch_id`` is stored as a plain string because the DOM ``text``
 * input always exposes ``value`` as a string; the empty string is
 * normalised to ``null`` on submit so the backend receives a NULL FK
 * instead of the literal string ``""`` (which would fail UUID
 * validation at 422).
 */
interface MigrationIdMapFormState {
  project_id: string;
  category: string;
  source_key: string;
  target_id: string;
  batch_id: string;
}

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: MigrationIdMapFormState = {
  project_id: "",
  category: "",
  source_key: "",
  target_id: "",
  batch_id: "",
};

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
 * Normalise an optional UUID-bearing text input to ``string | null``.
 * The backend ``MigrationIdMapCreate.batch_id`` and
 * ``MigrationIdMapUpdate.batch_id`` schemas accept ``null`` to clear
 * the FK, so blank input must become ``null`` — sending ``""`` would
 * be rejected as an invalid UUID (HTTP 422).
 */
function parseOptionalUuid(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed;
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

function MigrationIdMapPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<MigrationIdMapRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [sourceKeyFilter, setSourceKeyFilter] = useState("");
  const [batchFilter, setBatchFilter] = useState("");

  const [detail, setDetail] = useState<MigrationIdMapRead | null>(null);
  const [form, setForm] = useState<MigrationIdMapFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<MigrationIdMapRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            category: categoryFilter.trim() || undefined,
            source_key: sourceKeyFilter.trim() || undefined,
            batch_id: batchFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load migration ID maps.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, categoryFilter, sourceKeyFilter, batchFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<MigrationIdMapRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load migration ID map.";
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
        const row = await api.get<MigrationIdMapRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          category: row.category,
          source_key: row.source_key,
          target_id: row.target_id,
          batch_id: row.batch_id ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load migration ID map.";
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
        "Delete this migration ID map row? migration_id_map has no inbound foreign keys, so a hard delete only drops this crosswalk entry — the mapped target row in its own table is untouched. Re-running the batch will regenerate the mapping if the source row still exists.",
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
          : "Failed to delete migration ID map.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: MigrationIdMapCreate = {
        project_id: form.project_id.trim(),
        category: form.category.trim(),
        source_key: form.source_key.trim(),
        target_id: form.target_id.trim(),
        batch_id: parseOptionalUuid(form.batch_id),
      };
      await api.post<MigrationIdMapRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create migration ID map.";
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
      // ``project_id``, ``category`` and ``source_key`` are immutable
      // after create (see backend/schemas/migration_id_map.py — the
      // natural key triple is uniquely constrained and must not be
      // rewritten) so they are excluded.
      const payload: MigrationIdMapUpdate = {
        target_id: form.target_id.trim(),
        batch_id: parseOptionalUuid(form.batch_id),
      };
      await api.patch<MigrationIdMapRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update migration ID map.";
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
            Migration ID maps
          </h2>
          <p className="text-sm text-gray-600">
            Legacy Btrieve → PostgreSQL key crosswalk — one row per
            (project, category, source_key) triple mapping to a new
            target UUID. The natural key triple is uniquely constrained
            and immutable after create; only target_id and batch_id can
            be amended.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new migration ID map"
          >
            New ID Map
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
        <MigrationIdMapList
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
          sourceKeyFilter={sourceKeyFilter}
          onSourceKeyFilterChange={(value) => {
            setSkip(0);
            setSourceKeyFilter(value);
          }}
          batchFilter={batchFilter}
          onBatchFilterChange={(value) => {
            setSkip(0);
            setBatchFilter(value);
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
        <MigrationIdMapDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <MigrationIdMapForm
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

interface MigrationIdMapListProps {
  items: MigrationIdMapRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  categoryFilter: string;
  onCategoryFilterChange: (value: string) => void;
  sourceKeyFilter: string;
  onSourceKeyFilterChange: (value: string) => void;
  batchFilter: string;
  onBatchFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function MigrationIdMapList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  categoryFilter,
  onCategoryFilterChange,
  sourceKeyFilter,
  onSourceKeyFilterChange,
  batchFilter,
  onBatchFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: MigrationIdMapListProps) {
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
            title="Enter a canonical UUID, or leave blank to show ID maps across all projects."
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
            htmlFor="source-key-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Source key
          </label>
          <input
            id="source-key-filter"
            type="text"
            value={sourceKeyFilter}
            onChange={(event) => onSourceKeyFilterChange(event.target.value)}
            maxLength={255}
            placeholder="Legacy Btrieve key"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="batch-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Batch ID
          </label>
          <input
            id="batch-filter"
            type="text"
            value={batchFilter}
            onChange={(event) => onBatchFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID for the originating migration batch, or leave blank."
            placeholder="UUID — blank = any batch"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} ID map{total === 1 ? "" : "s"} total
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
                Source key
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Target ID
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Batch
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
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading migration ID maps…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No migration ID maps match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs uppercase text-gray-700">
                    {item.category}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-700">
                    {item.source_key}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-700">
                    {item.target_id}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.batch_id ?? "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.project_id}
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

interface MigrationIdMapDetailProps {
  row: MigrationIdMapRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function MigrationIdMapDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: MigrationIdMapDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading migration ID map…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Migration ID map not found.</p>
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
            Source key
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.source_key}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Target ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.target_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Batch ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.batch_id ?? "—"}
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

interface MigrationIdMapFormProps {
  form: MigrationIdMapFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: MigrationIdMapFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function MigrationIdMapForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: MigrationIdMapFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<MigrationIdMapFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading migration ID map…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit migration ID map" : "Create migration ID map"}
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
            htmlFor="source_key"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Source key
            <span className="ml-1 text-xs font-normal text-gray-500">
              (max 255 chars; immutable after create)
            </span>
          </label>
          <input
            id="source_key"
            type="text"
            value={form.source_key}
            onChange={(event) => patch({ source_key: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            minLength={1}
            maxLength={255}
            placeholder="Legacy Btrieve key"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="target_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Target ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (36-char PostgreSQL UUID; not an FK)
            </span>
          </label>
          <input
            id="target_id"
            type="text"
            value={form.target_id}
            onChange={(event) => patch({ target_id: event.target.value })}
            required
            minLength={1}
            maxLength={36}
            pattern={UUID_PATTERN}
            title="Enter the new PostgreSQL UUID the source key maps to (canonical UUID format)."
            placeholder="e.g. 0ad4c7d8-…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="batch_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Batch ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → migration_batch, ON DELETE SET NULL)
            </span>
          </label>
          <input
            id="batch_id"
            type="text"
            value={form.batch_id}
            onChange={(event) => patch({ batch_id: event.target.value })}
            pattern={UUID_PATTERN}
            title="Enter the originating migration batch UUID, or leave blank to clear the reference."
            placeholder="Blank = no originating batch"
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

export default MigrationIdMapPage;
