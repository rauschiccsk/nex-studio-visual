/**
 * BugFixTask admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 BugFixTask CRUD surface against the backend REST router
 * mounted at ``/api/v1/bug-fix-tasks`` (see
 * ``backend/api/routes/bug_fix_tasks.py``). The page is self-contained: it
 * owns its own local state rather than reaching for the global
 * ``bugStore`` because that store (per DESIGN.md § 3.3) is scoped to the
 * end-user bug workflow (open / resolved counts surfaced inside the
 * project area). The admin CRUD surface is a distinct concern that does
 * not need to mutate the application-wide bug counters. When ``bugStore``
 * adds dedicated admin actions in a later feat this page can switch over
 * without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with bug-id, status and task_type
 *     filters, plus row-level "View", "Edit" and "Delete" actions.
 *   - ``detail`` — read-only view of a single bug fix task, including
 *     timing fields and the audit columns.
 *   - ``create`` — form that ``POST``s a new bug fix task. ``bug_id`` is
 *     captured here because the backend schema (see
 *     ``backend/schemas/bug_fix_task.py``) requires it and treats it as
 *     immutable afterwards. ``number`` is auto-assigned by the service
 *     layer as ``MAX(number) + 1`` per bug, so it is intentionally
 *     absent from the form.
 *   - ``edit``   — form that ``PATCH``es only the mutable fields
 *     (``title``, ``description``, ``task_type``, ``status``,
 *     ``estimated_minutes``, ``actual_minutes``, ``checklist_type``).
 *     ``bug_id`` and ``number`` are rendered read-only.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 *
 * This page sits under ``/admin/bug-fix-tasks`` alongside the other Feat
 * 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/guardian-precedents``).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  BugFixTaskCreate,
  BugFixTaskRead,
  BugFixTaskStatus,
  BugFixTaskType,
  BugFixTaskUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the BugFixTask router (see backend/main.py). */
const ENDPOINT = "/bug-fix-tasks";

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
 * ``estimated_minutes`` and ``actual_minutes`` are modelled as strings
 * because the DOM ``number`` input always exposes ``value`` as a string.
 * They are parsed on submit into the ``number | null`` payload expected
 * by the backend.
 */
interface BugFixTaskFormState {
  bug_id: string;
  title: string;
  description: string;
  task_type: BugFixTaskType;
  status: BugFixTaskStatus;
  estimated_minutes: string;
  actual_minutes: string;
  checklist_type: string;
}

/** Selectable task types; mirrors the ``BugFixTaskType`` literal union. */
const TASK_TYPE_OPTIONS: readonly BugFixTaskType[] = [
  "backend",
  "frontend",
  "migration",
  "test",
  "docs",
] as const;

/** Selectable statuses; mirrors the ``BugFixTaskStatus`` literal union. */
const STATUS_OPTIONS: readonly BugFixTaskStatus[] = [
  "todo",
  "in_progress",
  "done",
  "failed",
] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: BugFixTaskFormState = {
  bug_id: "",
  title: "",
  description: "",
  task_type: "backend",
  status: "todo",
  estimated_minutes: "",
  actual_minutes: "",
  checklist_type: "",
};

/** Tailwind helper for task-type pills. */
function taskTypeBadgeClass(taskType: BugFixTaskType): string {
  switch (taskType) {
    case "backend":
      return "bg-blue-100 text-blue-800";
    case "frontend":
      return "bg-purple-100 text-purple-800";
    case "migration":
      return "bg-amber-100 text-amber-800";
    case "test":
      return "bg-emerald-100 text-emerald-800";
    case "docs":
      return "bg-slate-100 text-slate-800";
  }
}

/** Tailwind helper for status pills. */
function statusBadgeClass(taskStatus: BugFixTaskStatus): string {
  switch (taskStatus) {
    case "todo":
      return "bg-sky-100 text-sky-800";
    case "in_progress":
      return "bg-amber-100 text-amber-800";
    case "done":
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
 * blank input yields ``null``; the backend treats the field as nullable.
 * Decimal input is truncated to an integer because both ``estimated_minutes``
 * and ``actual_minutes`` are integer columns at the DB layer.
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
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend). Rendered on the ``bug_id``
 * inputs so obvious typos are caught by the browser's constraint-validation
 * API before the form is submitted — the backend would otherwise reject
 * them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** Render ``value ?? "—"`` for nullable detail fields. */
function renderNullable(value: string | number | null): string {
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

function BugFixTaskPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<BugFixTaskRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [bugFilter, setBugFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<BugFixTaskStatus | "">("");
  const [taskTypeFilter, setTaskTypeFilter] = useState<BugFixTaskType | "">("");

  const [detail, setDetail] = useState<BugFixTaskRead | null>(null);
  const [form, setForm] = useState<BugFixTaskFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<BugFixTaskRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            bug_id: bugFilter.trim() || undefined,
            status: statusFilter || undefined,
            task_type: taskTypeFilter || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load bug fix tasks.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, bugFilter, statusFilter, taskTypeFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<BugFixTaskRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load bug fix task.";
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
        const row = await api.get<BugFixTaskRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          bug_id: row.bug_id,
          title: row.title,
          description: row.description,
          task_type: row.task_type,
          status: row.status,
          estimated_minutes:
            row.estimated_minutes === null ? "" : String(row.estimated_minutes),
          actual_minutes:
            row.actual_minutes === null ? "" : String(row.actual_minutes),
          checklist_type: row.checklist_type ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load bug fix task.";
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
        "Delete this bug fix task? Setting Status = failed via Edit is the preferred soft-disable path. Hard delete nulls out delegations.bug_fix_task_id (ON DELETE SET NULL) so audit history is preserved.",
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
          : "Failed to delete bug fix task.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: BugFixTaskCreate = {
        bug_id: form.bug_id.trim(),
        title: form.title.trim(),
        description: form.description,
        task_type: form.task_type,
        status: form.status,
        estimated_minutes: parseOptionalInt(form.estimated_minutes),
        actual_minutes: parseOptionalInt(form.actual_minutes),
        checklist_type: parseOptionalText(form.checklist_type),
      };
      await api.post<BugFixTaskRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create bug fix task.";
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
      // ``bug_id`` and ``number`` are immutable after create (see
      // backend/schemas/bug_fix_task.py) so they are excluded.
      const payload: BugFixTaskUpdate = {
        title: form.title.trim(),
        description: form.description,
        task_type: form.task_type,
        status: form.status,
        estimated_minutes: parseOptionalInt(form.estimated_minutes),
        actual_minutes: parseOptionalInt(form.actual_minutes),
        checklist_type: parseOptionalText(form.checklist_type),
      };
      await api.patch<BugFixTaskRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update bug fix task.";
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
            Bug fix tasks
          </h2>
          <p className="text-sm text-gray-600">
            Bug-scoped fix-task registry — task_type drives delegation
            routing, status drives lifecycle, and ``number`` is auto-assigned
            per bug.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new bug fix task"
          >
            New Bug Fix Task
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
        <BugFixTaskList
          items={items}
          total={total}
          isLoading={isLoading}
          bugFilter={bugFilter}
          onBugFilterChange={(value) => {
            setSkip(0);
            setBugFilter(value);
          }}
          statusFilter={statusFilter}
          onStatusFilterChange={(value) => {
            setSkip(0);
            setStatusFilter(value);
          }}
          taskTypeFilter={taskTypeFilter}
          onTaskTypeFilterChange={(value) => {
            setSkip(0);
            setTaskTypeFilter(value);
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
        <BugFixTaskDetail
          task={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <BugFixTaskForm
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

interface BugFixTaskListProps {
  items: BugFixTaskRead[];
  total: number;
  isLoading: boolean;
  bugFilter: string;
  onBugFilterChange: (value: string) => void;
  statusFilter: BugFixTaskStatus | "";
  onStatusFilterChange: (value: BugFixTaskStatus | "") => void;
  taskTypeFilter: BugFixTaskType | "";
  onTaskTypeFilterChange: (value: BugFixTaskType | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function BugFixTaskList({
  items,
  total,
  isLoading,
  bugFilter,
  onBugFilterChange,
  statusFilter,
  onStatusFilterChange,
  taskTypeFilter,
  onTaskTypeFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: BugFixTaskListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="bug-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Bug ID
          </label>
          <input
            id="bug-filter"
            type="text"
            value={bugFilter}
            onChange={(event) => onBugFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show fix tasks across all bugs."
            placeholder="UUID — blank = all bugs"
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
              onStatusFilterChange(event.target.value as BugFixTaskStatus | "")
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
            htmlFor="task-type-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Task type
          </label>
          <select
            id="task-type-filter"
            value={taskTypeFilter}
            onChange={(event) =>
              onTaskTypeFilterChange(
                event.target.value as BugFixTaskType | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            {TASK_TYPE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} bug fix task{total === 1 ? "" : "s"} total
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
                Type
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
                Est. min
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Actual min
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
                  Loading bug fix tasks…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No bug fix tasks match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs text-gray-700">
                    #{item.number}
                  </td>
                  <td className="px-4 py-2 text-sm font-medium text-gray-900">
                    {item.title}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${taskTypeBadgeClass(item.task_type)}`}
                    >
                      {item.task_type}
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
                    {item.estimated_minutes ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-700">
                    {item.actual_minutes ?? "—"}
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

interface BugFixTaskDetailProps {
  task: BugFixTaskRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function BugFixTaskDetail({
  task,
  isLoading,
  onBack,
  onEdit,
}: BugFixTaskDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading bug fix task…
      </div>
    );
  }
  if (!task) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Bug fix task not found.</p>
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
            {task.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Number
          </dt>
          <dd className="font-mono text-sm text-gray-900">#{task.number}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Title
          </dt>
          <dd className="text-sm text-gray-900">{task.title}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Task type
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${taskTypeBadgeClass(task.task_type)}`}
            >
              {task.task_type}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Status
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(task.status)}`}
            >
              {task.status}
            </span>
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Description
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900">
            {task.description || "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Estimated minutes
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(task.estimated_minutes)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Actual minutes
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(task.actual_minutes)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Checklist type
          </dt>
          <dd className="text-sm text-gray-900">
            {renderNullable(task.checklist_type)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Bug ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {task.bug_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(task.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Updated at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(task.updated_at)}
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

interface BugFixTaskFormProps {
  form: BugFixTaskFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: BugFixTaskFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function BugFixTaskForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: BugFixTaskFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<BugFixTaskFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading bug fix task…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit bug fix task" : "Create bug fix task"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="bug_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Bug ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; immutable after create)
            </span>
          </label>
          <input
            id="bug_id"
            type="text"
            value={form.bug_id}
            onChange={(event) => patch({ bug_id: event.target.value })}
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
            placeholder="e.g. Fix login regression for non-ASCII passwords"
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
              (optional; what to change and how)
            </span>
          </label>
          <textarea
            id="description"
            value={form.description}
            onChange={(event) => patch({ description: event.target.value })}
            rows={5}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="task_type"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Task type
          </label>
          <select
            id="task_type"
            value={form.task_type}
            onChange={(event) =>
              patch({ task_type: event.target.value as BugFixTaskType })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {TASK_TYPE_OPTIONS.map((option) => (
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
              patch({ status: event.target.value as BugFixTaskStatus })
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
            htmlFor="estimated_minutes"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Estimated minutes
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional)
            </span>
          </label>
          <input
            id="estimated_minutes"
            type="number"
            min={0}
            step={1}
            value={form.estimated_minutes}
            onChange={(event) =>
              patch({ estimated_minutes: event.target.value })
            }
            placeholder="e.g. 30"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="actual_minutes"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Actual minutes
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; usually filled after completion)
            </span>
          </label>
          <input
            id="actual_minutes"
            type="number"
            min={0}
            step={1}
            value={form.actual_minutes}
            onChange={(event) => patch({ actual_minutes: event.target.value })}
            placeholder="e.g. 45"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="checklist_type"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Checklist type
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; max 30 chars; injected into CC delegation context)
            </span>
          </label>
          <input
            id="checklist_type"
            type="text"
            value={form.checklist_type}
            onChange={(event) => patch({ checklist_type: event.target.value })}
            maxLength={30}
            placeholder="e.g. backend-fix"
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

export default BugFixTaskPage;
