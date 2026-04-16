/**
 * ExecutionLog admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ExecutionLog CRUD surface against the backend REST
 * router mounted at ``/api/v1/execution-logs`` (see
 * ``backend/api/routes/execution_logs.py``). One ``execution_logs`` row
 * is the terminal record of a single CC-agent delegation — carrying
 * wall-clock duration, token usage, USD cost and commit-verification
 * state (DESIGN.md §1.19 ExecutionLog, §1.7 ``execution_logs`` table) —
 * and is the raw data behind the ``DelegationStatus`` / ``CCOutput``
 * live-output panels (DESIGN.md §3.1) and the ``ProjectMetricsCard``
 * reporting view (DESIGN.md §3.2).
 *
 * Like the other Feat 6 admin pages (``DelegationAdminPage``,
 * ``AutoFixAttemptPage``, ``FeatPage``, ``TaskAdminPage``, …) this
 * surface is deliberately self-contained rather than reaching for the
 * global ``delegationStore`` / ``reportStore``: per DESIGN.md §3.3
 * those stores back the end-user live-output and reporting surfaces,
 * which is a distinct concern from a per-row administrative CRUD
 * editor. When the stores grow dedicated admin actions in a later feat
 * this page can switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``delegation_id`` /
 *     ``task_id`` / ``status`` / ``commit_verified``, with row-level
 *     "View", "Edit" and "Delete" actions. Results are ordered by
 *     ``created_at DESC`` so the most recently recorded executions
 *     appear first — matching the service-layer ordering owned by
 *     ``backend/services/execution_log.py`` and the reporting views
 *     convention (latest activity at the top, DESIGN.md §1.19).
 *   - ``detail`` — read-only view of a single execution log: primary
 *     key, parent references (``delegation_id`` / ``task_id``), the
 *     terminal ``status``, the duration / token / cost metrics, the
 *     reported ``commit_hash`` and its ``commit_verified`` flag, plus
 *     audit timestamps (``created_at`` / ``updated_at``).
 *   - ``create`` — form that ``POST``s a new execution log.
 *     ``delegation_id`` and ``status`` are required; ``task_id``,
 *     ``duration_seconds``, ``input_tokens``, ``output_tokens``,
 *     ``total_cost_usd``, ``commit_hash`` and ``commit_verified`` are
 *     optional. ``commit_verified`` defaults to ``false`` via the DB
 *     ``server_default`` when omitted — the flag is flipped to ``true``
 *     only after the GitHub-API verification job confirms the reported
 *     ``commit_hash`` exists on the target branch (DESIGN.md §1.7
 *     "Commit verification"). Invalid / missing FK references
 *     (``delegation_id``, ``task_id``) are rejected by the DB-level FKs
 *     and surface as HTTP 422.
 *   - ``edit``   — form that ``PATCH``es the mutable metric /
 *     verification fields (``status``, ``duration_seconds``,
 *     ``input_tokens``, ``output_tokens``, ``total_cost_usd``,
 *     ``commit_hash``, ``commit_verified``). ``delegation_id`` and
 *     ``task_id`` are rendered read-only — the log's parent references
 *     are immutable by design (:class:`ExecutionLogUpdate`
 *     deliberately omits both, see
 *     ``backend/schemas/execution_log.py``) because the DB handles
 *     orphaning automatically via ``ON DELETE CASCADE`` on
 *     ``delegation_id`` and ``ON DELETE SET NULL`` on ``task_id``.
 *     PATCH semantics: fields that are blank / ``null`` are treated as
 *     "leave unchanged" by the service; the explicit-null transitions
 *     (``duration_seconds -> NULL``, ``input_tokens -> NULL``,
 *     ``output_tokens -> NULL``, ``total_cost_usd -> NULL``,
 *     ``commit_hash -> NULL``) are rare corrections that belong to
 *     admin tooling and are not expressible through this UI.
 *     ``commit_verified`` is a boolean and is always sent on edit so
 *     the GitHub verification job can flip it freely.
 *
 * ``DELETE`` is a hard delete. ``execution_logs`` has no inbound
 * foreign keys, so no RESTRICT dependency check is required — simply
 * drop the row. Routine operation retains the full execution history
 * for reporting (DESIGN.md §1.7); delete is reserved for test-fixture
 * cleanup and admin redaction. Deleting the parent ``delegations`` row
 * cascades automatically via ``execution_logs.delegation_id ON DELETE
 * CASCADE`` — that is the usual path for wiping a delegation's log.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / status values / constraint failures / decimal
 * overflow to HTTP 422 and they are shown verbatim in the inline error
 * banner.
 *
 * This page sits under ``/admin/execution-logs`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``, ``/admin/tasks``,
 * ``/admin/feats``, ``/admin/epics``, ``/admin/auto-fix-attempts``,
 * ``/admin/delegations``, …). It is distinct from the end-user
 * ``DelegationPage`` (``DelegationStatus`` / ``CCOutput`` live-output
 * surface at ``/projects/:slug/delegate``, DESIGN.md §3.1) and the
 * ``ProjectMetricsCard`` reporting view (DESIGN.md §3.2).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  ExecutionLogCreate,
  ExecutionLogRead,
  ExecutionLogStatus,
  ExecutionLogUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the ExecutionLog router (see backend/main.py). */
const ENDPOINT = "/execution-logs";

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
 * ``number`` / ``select`` / ``checkbox`` input ``value`` is always a
 * string (booleans read via ``checked``). UUID inputs enforce the
 * canonical shape via the ``pattern`` attribute and the backend rejects
 * malformed values with HTTP 422. ``status`` is cast to its literal
 * union at submit time.
 *
 * Numeric fields (``duration_seconds``, ``input_tokens``,
 * ``output_tokens``) are captured as strings and converted at submit
 * time; blank = unset (backend default on create; "leave unchanged" on
 * edit). ``total_cost_usd`` is a ``DECIMAL(10, 6)`` string per the wire
 * contract and is sent verbatim — client-side we only sanity-check it
 * against ``DECIMAL_10_6_PATTERN`` so the backend 422 doesn't cost a
 * round-trip on obvious typos.
 */
interface ExecutionLogFormState {
  delegation_id: string;
  task_id: string;
  status: ExecutionLogStatus;
  duration_seconds: string;
  input_tokens: string;
  output_tokens: string;
  total_cost_usd: string;
  commit_hash: string;
  commit_verified: boolean;
}

/**
 * Selectable statuses; mirrors the ``ExecutionLogStatus`` literal union
 * and the ``ck_execution_logs_status`` DB CHECK constraint
 * (``done | failed``).
 */
const STATUS_OPTIONS: readonly ExecutionLogStatus[] = ["done", "failed"] as const;

/**
 * Fresh-form defaults for the create mode.
 *
 * ``status`` defaults to ``done`` — the happy-path terminal state. The
 * unhappy path (``failed``) is explicitly selected by the operator so
 * the status field cannot silently hide a failure. ``commit_verified``
 * mirrors the DB ``server_default='false'``; the GitHub verification
 * job flips it to ``true`` later.
 */
const EMPTY_FORM: ExecutionLogFormState = {
  delegation_id: "",
  task_id: "",
  status: "done",
  duration_seconds: "",
  input_tokens: "",
  output_tokens: "",
  total_cost_usd: "",
  commit_hash: "",
  commit_verified: false,
};

/** Tailwind helper for status pills. */
function statusBadgeClass(value: ExecutionLogStatus): string {
  switch (value) {
    case "done":
      return "bg-emerald-100 text-emerald-800";
    case "failed":
      return "bg-red-100 text-red-800";
  }
}

/** Tailwind helper for the commit-verification pill. */
function verifiedBadgeClass(value: boolean): string {
  return value
    ? "bg-emerald-100 text-emerald-800"
    : "bg-gray-100 text-gray-700";
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

/** Render an optional UUID / string for the detail / list views. */
function formatOptional(value: string | null | undefined): string {
  return value === null || value === undefined || value.length === 0
    ? "—"
    : value;
}

/** Render an optional integer field for the detail / list views. */
function formatOptionalNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

/**
 * Format a ``DECIMAL(10, 6)`` cost value for display. The backend emits
 * well-formed strings such as ``"0.123456"``; we render them verbatim —
 * no locale formatting — so the admin surface shows the exact stored
 * value. Falls through for unexpected shapes so truncated / future rows
 * render without throwing.
 */
function formatCost(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return value;
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

/**
 * HTML ``pattern`` for a git commit hash (1–40 lowercase-or-uppercase
 * hex chars). The backend caps the column at 40 but accepts any
 * prefix; the client mirrors that contract.
 */
const COMMIT_HASH_PATTERN = "[0-9a-fA-F]{1,40}";

/**
 * HTML ``pattern`` expression for the ``DECIMAL(10, 6)`` cost column —
 * up to four leading digits before the optional decimal point and up
 * to six fractional digits afterwards. Matches the ``max_digits=10,
 * decimal_places=6`` constraint on ``execution_logs.total_cost_usd``.
 * Non-matching values still get rejected server-side with HTTP 422 —
 * this is a front-line ergonomic check only.
 */
const DECIMAL_10_6_PATTERN = "\\d{1,4}(\\.\\d{1,6})?";

/**
 * Parse a user-entered integer string into ``number | null``. Blank =
 * ``null`` (server default on create; "leave unchanged" on edit).
 * Returns ``NaN`` for malformed input so the caller can raise an
 * explicit validation error instead of silently sending garbage.
 */
function parseOptionalInt(raw: string): number | null | typeof NaN {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
    return Number.NaN;
  }
  return value;
}

function ExecutionLogPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ExecutionLogRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [delegationFilter, setDelegationFilter] = useState("");
  const [taskFilter, setTaskFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<ExecutionLogStatus | "">("");
  const [commitVerifiedFilter, setCommitVerifiedFilter] = useState<
    "" | "true" | "false"
  >("");

  const [detail, setDetail] = useState<ExecutionLogRead | null>(null);
  const [form, setForm] = useState<ExecutionLogFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ExecutionLogRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            delegation_id: delegationFilter.trim() || undefined,
            task_id: taskFilter.trim() || undefined,
            // Backend exposes the parameter as ``status`` (alias of
            // ``status_filter`` — see
            // backend/api/routes/execution_logs.py).
            status: statusFilter || undefined,
            commit_verified:
              commitVerifiedFilter === ""
                ? undefined
                : commitVerifiedFilter === "true",
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load execution logs.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [
    skip,
    delegationFilter,
    taskFilter,
    statusFilter,
    commitVerifiedFilter,
  ]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ExecutionLogRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load execution log.";
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
        const row = await api.get<ExecutionLogRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          delegation_id: row.delegation_id,
          task_id: row.task_id ?? "",
          status: row.status,
          duration_seconds:
            row.duration_seconds === null ? "" : String(row.duration_seconds),
          input_tokens:
            row.input_tokens === null ? "" : String(row.input_tokens),
          output_tokens:
            row.output_tokens === null ? "" : String(row.output_tokens),
          total_cost_usd: row.total_cost_usd ?? "",
          commit_hash: row.commit_hash ?? "",
          commit_verified: row.commit_verified,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load execution log.";
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
        "Delete this execution log? This is a hard delete. execution_logs has no inbound foreign keys, so no RESTRICT dependency check is required. Routine operation retains the full execution history for reporting; delete is reserved for test-fixture cleanup and admin redaction. Proceed?",
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
          : "Failed to delete execution log.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const delegation = form.delegation_id.trim();
      if (delegation.length === 0) {
        throw new Error("Delegation ID is required.");
      }
      const duration = parseOptionalInt(form.duration_seconds);
      if (Number.isNaN(duration)) {
        throw new Error(
          "Duration (seconds) must be a non-negative integer.",
        );
      }
      const inputTokens = parseOptionalInt(form.input_tokens);
      if (Number.isNaN(inputTokens)) {
        throw new Error("Input tokens must be a non-negative integer.");
      }
      const outputTokens = parseOptionalInt(form.output_tokens);
      if (Number.isNaN(outputTokens)) {
        throw new Error("Output tokens must be a non-negative integer.");
      }
      const commit = form.commit_hash.trim();
      const cost = form.total_cost_usd.trim();
      const payload: ExecutionLogCreate = {
        delegation_id: delegation,
        task_id: form.task_id.trim() || null,
        status: form.status,
        duration_seconds: duration as number | null,
        input_tokens: inputTokens as number | null,
        output_tokens: outputTokens as number | null,
        total_cost_usd: cost.length === 0 ? null : cost,
        commit_hash: commit.length === 0 ? null : commit,
        commit_verified: form.commit_verified,
      };
      await api.post<ExecutionLogRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to create execution log.";
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
      // Only the mutable metric / verification fields are sent — parent
      // FKs (``delegation_id``, ``task_id``) are immutable (see
      // backend/schemas/execution_log.py — ExecutionLogUpdate
      // deliberately omits them). Blank numeric / string values are
      // dropped (service treats them as "leave unchanged").
      // ``commit_verified`` is always sent because it is a boolean and
      // the GitHub verification job must be able to flip it freely.
      const duration = parseOptionalInt(form.duration_seconds);
      if (Number.isNaN(duration)) {
        throw new Error(
          "Duration (seconds) must be a non-negative integer.",
        );
      }
      const inputTokens = parseOptionalInt(form.input_tokens);
      if (Number.isNaN(inputTokens)) {
        throw new Error("Input tokens must be a non-negative integer.");
      }
      const outputTokens = parseOptionalInt(form.output_tokens);
      if (Number.isNaN(outputTokens)) {
        throw new Error("Output tokens must be a non-negative integer.");
      }
      const commit = form.commit_hash.trim();
      const cost = form.total_cost_usd.trim();
      const payload: ExecutionLogUpdate = {
        status: form.status,
        commit_verified: form.commit_verified,
      };
      if (duration !== null) {
        payload.duration_seconds = duration as number;
      }
      if (inputTokens !== null) {
        payload.input_tokens = inputTokens as number;
      }
      if (outputTokens !== null) {
        payload.output_tokens = outputTokens as number;
      }
      if (cost.length > 0) {
        payload.total_cost_usd = cost;
      }
      if (commit.length > 0) {
        payload.commit_hash = commit;
      }
      await api.patch<ExecutionLogRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to update execution log.";
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
            Execution logs
          </h2>
          <p className="text-sm text-gray-600">
            Per-delegation terminal records carrying duration, token usage,
            cost and commit-verification state (DESIGN.md §1.19 / §1.7
            ``execution_logs`` table). An execution log belongs to exactly
            one delegation (``ON DELETE CASCADE``) and optionally one task
            (``ON DELETE SET NULL``). Ordered by ``created_at DESC`` (most
            recent first). ``commit_verified`` is flipped from ``false``
            to ``true`` only after the GitHub API verification job
            confirms the reported ``commit_hash``. Delete is a hard
            delete; routine operation retains the full execution history
            for reporting.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new execution log"
          >
            New execution log
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
        <ExecutionLogList
          items={items}
          total={total}
          isLoading={isLoading}
          delegationFilter={delegationFilter}
          onDelegationFilterChange={(value) => {
            setSkip(0);
            setDelegationFilter(value);
          }}
          taskFilter={taskFilter}
          onTaskFilterChange={(value) => {
            setSkip(0);
            setTaskFilter(value);
          }}
          statusFilter={statusFilter}
          onStatusFilterChange={(value) => {
            setSkip(0);
            setStatusFilter(value);
          }}
          commitVerifiedFilter={commitVerifiedFilter}
          onCommitVerifiedFilterChange={(value) => {
            setSkip(0);
            setCommitVerifiedFilter(value);
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
        <ExecutionLogDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ExecutionLogForm
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

interface ExecutionLogListProps {
  items: ExecutionLogRead[];
  total: number;
  isLoading: boolean;
  delegationFilter: string;
  onDelegationFilterChange: (value: string) => void;
  taskFilter: string;
  onTaskFilterChange: (value: string) => void;
  statusFilter: ExecutionLogStatus | "";
  onStatusFilterChange: (value: ExecutionLogStatus | "") => void;
  commitVerifiedFilter: "" | "true" | "false";
  onCommitVerifiedFilterChange: (value: "" | "true" | "false") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ExecutionLogList({
  items,
  total,
  isLoading,
  delegationFilter,
  onDelegationFilterChange,
  taskFilter,
  onTaskFilterChange,
  statusFilter,
  onStatusFilterChange,
  commitVerifiedFilter,
  onCommitVerifiedFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ExecutionLogListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="delegation-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Delegation ID
          </label>
          <input
            id="delegation-filter"
            type="text"
            value={delegationFilter}
            onChange={(event) => onDelegationFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show logs for a specific delegation. Blank = all."
            placeholder="UUID — blank = all"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="task-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Task ID
          </label>
          <input
            id="task-filter"
            type="text"
            value={taskFilter}
            onChange={(event) => onTaskFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show logs for a specific task. Blank = all."
            placeholder="UUID — blank = all"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
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
                event.target.value as ExecutionLogStatus | "",
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
            htmlFor="commit-verified-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Commit verified
          </label>
          <select
            id="commit-verified-filter"
            value={commitVerifiedFilter}
            onChange={(event) =>
              onCommitVerifiedFilterChange(
                event.target.value as "" | "true" | "false",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            <option value="true">Verified</option>
            <option value="false">Unverified</option>
          </select>
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} execution log{total === 1 ? "" : "s"} total
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
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Delegation
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Task
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Wall-clock duration of the CC delegation in seconds."
              >
                Duration (s)
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Input tokens
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Output tokens
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="DECIMAL(10, 6) USD."
              >
                Cost (USD)
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Commit
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Verified
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
                  colSpan={11}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading execution logs…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={11}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No execution logs match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td
                    className="px-4 py-2 font-mono text-[11px] text-gray-500"
                    title={item.delegation_id}
                  >
                    {item.delegation_id.slice(0, 8)}…
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.task_id ? (
                      <span title={item.task_id}>
                        {item.task_id.slice(0, 8)}…
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-2 text-right text-sm text-gray-900">
                    {formatOptionalNumber(item.duration_seconds)}
                  </td>
                  <td className="px-4 py-2 text-right text-sm text-gray-900">
                    {formatOptionalNumber(item.input_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right text-sm text-gray-900">
                    {formatOptionalNumber(item.output_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-900">
                    {formatCost(item.total_cost_usd)}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-700">
                    {item.commit_hash ? item.commit_hash.slice(0, 10) : "—"}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${verifiedBadgeClass(item.commit_verified)}`}
                    >
                      {item.commit_verified ? "verified" : "unverified"}
                    </span>
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

interface ExecutionLogDetailProps {
  row: ExecutionLogRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ExecutionLogDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ExecutionLogDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading execution log…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Execution log not found.</p>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Execution log ID
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
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Commit verified
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${verifiedBadgeClass(row.commit_verified)}`}
            >
              {row.commit_verified ? "verified" : "unverified"}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Delegation ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.delegation_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Task ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.task_id)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Duration (seconds)
          </dt>
          <dd className="text-sm text-gray-900">
            {formatOptionalNumber(row.duration_seconds)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Total cost (USD)
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatCost(row.total_cost_usd)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Input tokens
          </dt>
          <dd className="text-sm text-gray-900">
            {formatOptionalNumber(row.input_tokens)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Output tokens
          </dt>
          <dd className="text-sm text-gray-900">
            {formatOptionalNumber(row.output_tokens)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Commit hash
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {formatOptional(row.commit_hash)}
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

interface ExecutionLogFormProps {
  form: ExecutionLogFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ExecutionLogFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ExecutionLogForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ExecutionLogFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ExecutionLogFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading execution log…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit execution log" : "Create execution log"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label
            htmlFor="delegation_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Delegation ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required UUID; FK → delegations, ON DELETE CASCADE;
              immutable after create)
            </span>
          </label>
          <input
            id="delegation_id"
            type="text"
            value={form.delegation_id}
            onChange={(event) => patch({ delegation_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Delegation this execution log belongs to."
            placeholder="UUID"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="task_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Task ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → tasks, ON DELETE SET NULL; immutable
              after create)
            </span>
          </label>
          <input
            id="task_id"
            type="text"
            value={form.task_id}
            onChange={(event) => patch({ task_id: event.target.value })}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Optional task this execution targeted."
            placeholder="blank = none"
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
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; done | failed)
            </span>
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({ status: event.target.value as ExecutionLogStatus })
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

        <div className="flex items-end">
          <label
            htmlFor="commit_verified"
            className="inline-flex items-center gap-2 text-sm font-medium text-gray-700"
          >
            <input
              id="commit_verified"
              type="checkbox"
              checked={form.commit_verified}
              onChange={(event) =>
                patch({ commit_verified: event.target.checked })
              }
              className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            Commit verified
            <span className="text-xs font-normal text-gray-500">
              (server default ``false``; flipped to ``true`` by the
              GitHub verification job)
            </span>
          </label>
        </div>

        <div>
          <label
            htmlFor="duration_seconds"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Duration (seconds)
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; non-negative integer; blank = unset /
              unchanged)
            </span>
          </label>
          <input
            id="duration_seconds"
            type="number"
            min={0}
            step={1}
            value={form.duration_seconds}
            onChange={(event) =>
              patch({ duration_seconds: event.target.value })
            }
            placeholder="blank = unset"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="total_cost_usd"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Total cost (USD)
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; DECIMAL(10, 6); blank = unset / unchanged)
            </span>
          </label>
          <input
            id="total_cost_usd"
            type="text"
            inputMode="decimal"
            value={form.total_cost_usd}
            onChange={(event) =>
              patch({ total_cost_usd: event.target.value })
            }
            pattern={DECIMAL_10_6_PATTERN}
            title="Up to 4 digits before and 6 digits after the decimal point."
            placeholder="e.g. 0.123456"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="input_tokens"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Input tokens
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; non-negative integer)
            </span>
          </label>
          <input
            id="input_tokens"
            type="number"
            min={0}
            step={1}
            value={form.input_tokens}
            onChange={(event) => patch({ input_tokens: event.target.value })}
            placeholder="blank = unset"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="output_tokens"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Output tokens
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; non-negative integer)
            </span>
          </label>
          <input
            id="output_tokens"
            type="number"
            min={0}
            step={1}
            value={form.output_tokens}
            onChange={(event) => patch({ output_tokens: event.target.value })}
            placeholder="blank = unset"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="commit_hash"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Commit hash
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; up to 40 hex chars; blank = unset / unchanged)
            </span>
          </label>
          <input
            id="commit_hash"
            type="text"
            value={form.commit_hash}
            onChange={(event) => patch({ commit_hash: event.target.value })}
            maxLength={40}
            pattern={COMMIT_HASH_PATTERN}
            title="Git commit hash produced by the delegation — 1 to 40 hex characters."
            placeholder="blank = unset"
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

export default ExecutionLogPage;
