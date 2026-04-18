/**
 * ModuleDependency admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ModuleDependency CRUD surface against the backend
 * REST router mounted at ``/api/v1/module-dependencies`` (see
 * ``backend/api/routes/module_dependencies.py`` and its prefix in
 * ``backend/main.py``). A ``module_dependencies`` row is a single edge
 * in the per-project module DAG — it records that ``module_id``
 * requires ``depends_on_module_id`` to reach ``done`` first
 * (DESIGN.md §1.2 ``module_dependencies`` table, D-10 NEX Horizont
 * module seeding). These edges back the end-user ``ModuleGraph``
 * visualisation on ``ModuleRegistryPage`` (DESIGN.md §3.1, §3.2) and
 * the ``ModuleService.start_module()`` prerequisite check.
 *
 * Like the other Feat 6 admin pages (``ProjectModulePage``,
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
 *   - ``list``   — paginated table filtered by ``module_id`` and/or
 *     ``depends_on_module_id``, with row-level "View", "Edit" and
 *     "Delete" actions. The two filters map to the two canonical graph
 *     queries: "what does this module depend on" (outgoing edges) and
 *     "which modules depend on this one" (incoming edges).
 *   - ``detail`` — read-only view of a single edge including the
 *     ``(module_id, depends_on_module_id)`` natural key and audit
 *     timestamps.
 *   - ``create`` — form that ``POST``s a new edge. Both
 *     ``module_id`` and ``depends_on_module_id`` are required and must
 *     be canonical UUIDs referencing existing ``project_modules.id``
 *     rows. The form rejects obvious self-loops client-side; the
 *     backend also rejects them (HTTP 409) as well as duplicates
 *     (``UNIQUE(module_id, depends_on_module_id)``, HTTP 409).
 *   - ``edit``   — ``ModuleDependencyUpdate`` has no mutable fields
 *     (the natural key is immutable — an edge is either created or
 *     deleted, never rewritten in place; see
 *     ``backend/schemas/module_dependency.py`` and the router
 *     docstring). The "edit" mode is therefore a read-only
 *     confirmation form that ``PATCH``es the row and returns it
 *     unchanged — retained for CRUD-surface symmetry.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps duplicate
 * ``(module_id, depends_on_module_id)`` pairs and self-loops to
 * HTTP 409, non-existent rows to HTTP 404 and other validation
 * failures to HTTP 422; all are shown verbatim in the inline error
 * banner.
 *
 * This page sits under ``/admin/module-dependencies`` alongside the
 * other Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/project-modules``, ``/admin/bugs``, …). It is distinct
 * from ``ModuleRegistryPage`` (the end-user Module Registry
 * visualisation at ``/projects/:slug/modules``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  ModuleDependencyCreate,
  ModuleDependencyRead,
  ModuleDependencyUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the ModuleDependency router (see backend/main.py). */
const ENDPOINT = "/module-dependencies";

/** Page size used by the list view. Matches the backend default (capped at 100). */
const PAGE_SIZE = 20;

/** Finite mode state keeps the render logic explicit and linter-friendly. */
type Mode =
  | { kind: "list" }
  | { kind: "detail"; id: string }
  | { kind: "create" }
  | { kind: "edit"; id: string };

/**
 * Shape of the mutable fields in the create form.
 *
 * Both values are captured as plain strings because the DOM ``text``
 * input ``value`` is always a string; the canonical UUID format is
 * enforced client-side via the ``pattern`` attribute and re-validated
 * by the backend on submit.
 */
interface ModuleDependencyFormState {
  module_id: string;
  depends_on_module_id: string;
}

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: ModuleDependencyFormState = {
  module_id: "",
  depends_on_module_id: "",
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
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend). Rendered on UUID inputs so
 * obvious typos are caught by the browser's constraint-validation API
 * before the form is submitted — the backend would otherwise reject
 * them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

function ModuleDependencyPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ModuleDependencyRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [moduleFilter, setModuleFilter] = useState("");
  const [dependsOnFilter, setDependsOnFilter] = useState("");

  const [detail, setDetail] = useState<ModuleDependencyRead | null>(null);
  const [form, setForm] = useState<ModuleDependencyFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ModuleDependencyRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            module_id: moduleFilter.trim() || undefined,
            depends_on_module_id: dependsOnFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load module dependencies.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, moduleFilter, dependsOnFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ModuleDependencyRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load module dependency.";
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
    // ``ModuleDependencyUpdate`` has no mutable fields, so the form is
    // read-only — we still load the row so the detail panel renders
    // identically to the detail mode.
    if (mode.kind !== "edit") {
      return;
    }
    let cancelled = false;
    (async () => {
      setIsLoading(true);
      setError(null);
      try {
        const row = await api.get<ModuleDependencyRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setDetail(row);
        setForm({
          module_id: row.module_id,
          depends_on_module_id: row.depends_on_module_id,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load module dependency.";
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
        "Delete this dependency edge? This is the 'break dependency' flow — the two modules remain, only the edge between them is removed (module_dependencies has no inbound FKs).",
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
          : "Failed to delete module dependency.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const moduleId = form.module_id.trim();
      const dependsOnModuleId = form.depends_on_module_id.trim();
      if (moduleId === dependsOnModuleId && moduleId !== "") {
        // Pre-empt the backend 409 for the most common user error — a
        // module cannot depend on itself (DESIGN.md §1.2).
        throw new ApiError(
          409,
          "A module cannot depend on itself — self-loops are not allowed.",
        );
      }
      const payload: ModuleDependencyCreate = {
        module_id: moduleId,
        depends_on_module_id: dependsOnModuleId,
      };
      await api.post<ModuleDependencyRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create module dependency.";
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
      // ``ModuleDependencyUpdate`` deliberately exposes no fields — the
      // natural key ``(module_id, depends_on_module_id)`` is immutable
      // (see backend/schemas/module_dependency.py). We send an empty
      // payload so the backend returns the unmodified row; the call
      // is retained for CRUD-surface symmetry only.
      const payload: ModuleDependencyUpdate = {};
      await api.patch<ModuleDependencyRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update module dependency.";
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
            Module dependencies
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Edges in the per-project module DAG (DESIGN.md §1.2). Each
            row records that ``module_id`` requires
            ``depends_on_module_id`` to reach ``done`` first. The pair
            is unique and immutable — edges are created or deleted,
            never rewritten in place.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new module dependency"
          >
            New Dependency
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
        <ModuleDependencyList
          items={items}
          total={total}
          isLoading={isLoading}
          moduleFilter={moduleFilter}
          onModuleFilterChange={(value) => {
            setSkip(0);
            setModuleFilter(value);
          }}
          dependsOnFilter={dependsOnFilter}
          onDependsOnFilterChange={(value) => {
            setSkip(0);
            setDependsOnFilter(value);
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
        <ModuleDependencyDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {mode.kind === "create" && (
        <ModuleDependencyCreateForm
          form={form}
          isSaving={isSaving}
          onChange={setForm}
          onCancel={openList}
          onSubmit={handleCreate}
        />
      )}

      {mode.kind === "edit" && (
        <ModuleDependencyEditForm
          row={detail}
          isSaving={isSaving}
          isLoading={isLoading}
          onCancel={openList}
          onSubmit={handleUpdate}
        />
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*                              Sub-components                                */
/* -------------------------------------------------------------------------- */

interface ModuleDependencyListProps {
  items: ModuleDependencyRead[];
  total: number;
  isLoading: boolean;
  moduleFilter: string;
  onModuleFilterChange: (value: string) => void;
  dependsOnFilter: string;
  onDependsOnFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ModuleDependencyList({
  items,
  total,
  isLoading,
  moduleFilter,
  onModuleFilterChange,
  dependsOnFilter,
  onDependsOnFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ModuleDependencyListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="module-filter"
            className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Module ID
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (outgoing — "what does this module depend on")
            </span>
          </label>
          <input
            id="module-filter"
            type="text"
            value={moduleFilter}
            onChange={(event) => onModuleFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show edges across all modules."
            placeholder="UUID — blank = all modules"
            className="w-80 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="depends-on-filter"
            className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Depends-on Module ID
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (incoming — "which modules depend on this one")
            </span>
          </label>
          <input
            id="depends-on-filter"
            type="text"
            value={dependsOnFilter}
            onChange={(event) => onDependsOnFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show edges across all modules."
            placeholder="UUID — blank = all modules"
            className="w-80 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500 dark:text-gray-400">
          {total} edge{total === 1 ? "" : "s"} total
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
                Module
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Depends on
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400"
              >
                Edge ID
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
                  colSpan={5}
                  className="px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400"
                >
                  Loading module dependencies…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400"
                >
                  No module dependencies match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50 dark:bg-gray-900 dark:hover:bg-gray-800">
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-900 dark:text-gray-100">
                    {item.module_id}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-900 dark:text-gray-100">
                    {item.depends_on_module_id}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500 dark:text-gray-400">
                    {item.id}
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

interface ModuleDependencyDetailProps {
  row: ModuleDependencyRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ModuleDependencyDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ModuleDependencyDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 text-sm text-gray-600 dark:text-gray-400">
        Loading module dependency…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">Module dependency not found.</p>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 shadow-sm">
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Edge ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900 dark:text-gray-100">
            {row.id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Module ID
            <span className="ml-1 text-xs font-normal text-gray-400">
              (the dependent module)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {row.module_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Depends-on Module ID
            <span className="ml-1 text-xs font-normal text-gray-400">
              (the prerequisite)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {row.depends_on_module_id}
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

interface ModuleDependencyCreateFormProps {
  form: ModuleDependencyFormState;
  isSaving: boolean;
  onChange: (form: ModuleDependencyFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ModuleDependencyCreateForm({
  form,
  isSaving,
  onChange,
  onCancel,
  onSubmit,
}: ModuleDependencyCreateFormProps) {
  const patch = (fragment: Partial<ModuleDependencyFormState>) =>
    onChange({ ...form, ...fragment });

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        Create module dependency
      </h3>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Both UUIDs must reference existing ``project_modules.id`` rows
        belonging to the same project — the backend enforces the FK on
        commit. Self-loops and duplicate edges are rejected with
        HTTP 409.
      </p>

      <div className="grid grid-cols-1 gap-4">
        <div>
          <label
            htmlFor="module_id"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Module ID
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (UUID; the dependent module — FK → project_modules,
              ON DELETE CASCADE)
            </span>
          </label>
          <input
            id="module_id"
            type="text"
            value={form.module_id}
            onChange={(event) => patch({ module_id: event.target.value })}
            required
            pattern={UUID_PATTERN}
            title="Enter the UUID of the dependent module."
            placeholder="e.g. a31d1a12-…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="depends_on_module_id"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Depends-on Module ID
            <span className="ml-1 text-xs font-normal text-gray-500 dark:text-gray-400">
              (UUID; the prerequisite — FK → project_modules, ON DELETE
              CASCADE)
            </span>
          </label>
          <input
            id="depends_on_module_id"
            type="text"
            value={form.depends_on_module_id}
            onChange={(event) =>
              patch({ depends_on_module_id: event.target.value })
            }
            required
            pattern={UUID_PATTERN}
            title="Enter the UUID of the prerequisite module — must differ from the dependent module."
            placeholder="e.g. 7fcd8c42-…"
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
          {isSaving ? "Saving…" : "Create"}
        </button>
      </div>
    </form>
  );
}

interface ModuleDependencyEditFormProps {
  row: ModuleDependencyRead | null;
  isSaving: boolean;
  isLoading: boolean;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

/**
 * Read-only "edit" form.
 *
 * ``ModuleDependencyUpdate`` deliberately exposes no fields — the
 * natural key ``(module_id, depends_on_module_id)`` is immutable, so
 * an edge is either created or deleted, never rewritten in place
 * (see ``backend/schemas/module_dependency.py`` and the router
 * docstring in ``backend/api/routes/module_dependencies.py``). The
 * mode is retained for CRUD-surface symmetry: submitting issues a
 * ``PATCH`` that returns the unmodified row, which is useful as a
 * round-trip sanity check but does not mutate state.
 */
function ModuleDependencyEditForm({
  row,
  isSaving,
  isLoading,
  onCancel,
  onSubmit,
}: ModuleDependencyEditFormProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 text-sm text-gray-600 dark:text-gray-400">
        Loading module dependency…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">Module dependency not found.</p>
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Back to list
        </button>
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        Edit module dependency
      </h3>
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        The natural key ``(module_id, depends_on_module_id)`` is
        immutable — an edge is either created or deleted, never
        rewritten in place. This form exists for CRUD-surface symmetry
        only; submitting it issues a no-op ``PATCH`` and returns the
        row unchanged. To redirect an edge, delete it and create a new
        one.
      </div>

      <dl className="grid grid-cols-1 gap-4">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Edge ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900 dark:text-gray-100">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Module ID
            <span className="ml-1 text-xs font-normal text-gray-400">
              (the dependent module — immutable)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {row.module_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Depends-on Module ID
            <span className="ml-1 text-xs font-normal text-gray-400">
              (the prerequisite — immutable)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900 dark:text-gray-100">
            {row.depends_on_module_id}
          </dd>
        </div>
      </dl>

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
          {isSaving ? "Saving…" : "Save (no-op)"}
        </button>
      </div>
    </form>
  );
}

export default ModuleDependencyPage;
