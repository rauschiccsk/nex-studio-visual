/**
 * Delegation admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 Delegation CRUD surface against the backend REST
 * router mounted at ``/api/v1/delegations`` (see
 * ``backend/api/routes/delegations.py``). A ``delegations`` row is one
 * CC-agent invocation attached to at most one of ``task_id`` /
 * ``feat_id`` / ``bug_fix_task_id`` / ``bug_id`` (DESIGN.md §1.18
 * Delegation, §1.7 ``delegations`` table) and backs the end-user
 * ``DelegationPage`` / ``DelegationStatus`` / ``CCOutput`` live-output
 * UI (DESIGN.md §3.1).
 *
 * Like the other Feat 6 admin pages (``TaskAdminPage``, ``FeatPage``,
 * ``AutoFixAttemptPage``, ``ArchitectMessagePage``, …) this surface is
 * deliberately self-contained rather than reaching for the global
 * ``delegationStore``: per DESIGN.md §3.3 that store backs the
 * end-user ``DelegationPage`` live-output surface (CC streaming, phase
 * indicators, Guardian layer results), which is a distinct concern
 * from a per-row administrative CRUD editor. When the store grows
 * dedicated admin actions in a later feat this page can switch over
 * without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``task_id`` / ``feat_id``
 *     / ``bug_fix_task_id`` / ``bug_id`` / ``status`` / ``cc_agent``,
 *     with row-level "View", "Edit" and "Delete" actions. Results are
 *     ordered by ``started_at DESC`` so the most recently started
 *     delegations appear first — matching the service-layer ordering
 *     owned by ``backend/services/delegation.py`` and the
 *     ``DelegationPage`` "active delegation + live output" convention.
 *   - ``detail`` — read-only view of a single delegation: primary key,
 *     parent references, ``cc_agent``, ``prompt`` (full text),
 *     ``status``, ``raw_output`` (full stream), ``commit_hash`` and
 *     audit timestamps (``started_at`` / ``completed_at`` /
 *     ``created_at`` / ``updated_at``).
 *   - ``create`` — form that ``POST``s a new delegation. ``prompt`` is
 *     the only required field; ``cc_agent`` (``ubuntu_cc``), ``status``
 *     (``pending``) and ``started_at`` (``NOW()``) default to the DB-
 *     level ``server_default`` values when omitted. The four parent FKs
 *     (``task_id``, ``feat_id``, ``bug_fix_task_id``, ``bug_id``) are
 *     all optional — a delegation is linked to at most one work item
 *     per DESIGN.md §1.18, and all four use ``ON DELETE SET NULL`` at
 *     the DB level so the delegation row survives deletion of the
 *     originating work item. The service deliberately does NOT enforce
 *     an "exactly one parent" invariant, because admin / ad-hoc
 *     delegations legitimately have no parent at all; the form
 *     likewise leaves that policy to the operator.
 *   - ``edit``   — form that ``PATCH``es the mutable lifecycle fields
 *     (``status``, ``raw_output``, ``commit_hash``, ``started_at``,
 *     ``completed_at``). ``cc_agent``, ``prompt`` and the four parent
 *     FKs are rendered read-only — the agent identity and the prompt
 *     injected into the agent together form the delegation's
 *     execution contract and must not be rewritten post-creation
 *     (:class:`DelegationUpdate` deliberately omits them, see
 *     ``backend/schemas/delegation.py``). PATCH semantics: fields that
 *     are blank / ``null`` are treated as "leave unchanged" by the
 *     service; the explicit-null transitions (``raw_output -> NULL``,
 *     ``commit_hash -> NULL``, ``started_at -> NULL``,
 *     ``completed_at -> NULL``) are rare corrections that belong to
 *     admin tooling and are not expressible through this UI.
 *
 * ``DELETE`` is a hard delete. Inbound foreign keys on ``delegations``
 * — ``execution_logs.delegation_id`` (``ON DELETE CASCADE``),
 * ``guardian_reviews.delegation_id`` (``ON DELETE CASCADE``) and
 * ``auto_fix_attempts.delegation_id`` (``ON DELETE SET NULL``) — are
 * handled at the DB level, so dependent execution logs and guardian
 * reviews are cascaded on the way out and auto-fix attempts are
 * silently NULL-ed. No RESTRICT dependency check is required, but the
 * confirmation dialog calls out the cascade so the operator understands
 * the effect. Routine operation retains the full delegation history
 * for reporting (DESIGN.md §1.7); delete is reserved for test-fixture
 * cleanup and admin redaction.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / status values / cc-agent values / constraint failures
 * to HTTP 422 and they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/delegations`` alongside the other Feat
 * 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``, ``/admin/tasks``,
 * ``/admin/feats``, ``/admin/epics``, ``/admin/auto-fix-attempts``,
 * …). It is distinct from ``DelegationPage`` (the end-user
 * ``DelegationStatus`` / ``CCOutput`` live-output surface at
 * ``/projects/:slug/delegate``, DESIGN.md §3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  DelegationCCAgent,
  DelegationCreate,
  DelegationRead,
  DelegationStatus,
  DelegationUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the Delegation router (see backend/main.py). */
const ENDPOINT = "/delegations";

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
 * ``textarea`` / ``select`` / ``datetime-local`` input ``value`` is
 * always a string. UUID inputs enforce the canonical shape via the
 * ``pattern`` attribute and the backend rejects malformed values with
 * HTTP 422. ``status`` and ``cc_agent`` are cast to their literal
 * union at submit time.
 *
 * ``started_at`` and ``completed_at`` are captured as
 * ``datetime-local`` strings (``YYYY-MM-DDTHH:mm``) and converted to
 * ISO-8601 (with seconds, no timezone — backend accepts naive UTC) at
 * submit time. Blank = unset (backend default on create; "leave
 * unchanged" on edit).
 */
interface DelegationFormState {
  task_id: string;
  feat_id: string;
  bug_fix_task_id: string;
  bug_id: string;
  cc_agent: DelegationCCAgent;
  prompt: string;
  status: DelegationStatus;
  raw_output: string;
  commit_hash: string;
  started_at: string;
  completed_at: string;
}

/**
 * Selectable CC agents; mirrors the ``DelegationCCAgent`` literal
 * union and the ``ck_delegations_cc_agent`` DB CHECK constraint
 * (``ubuntu_cc``).
 */
const CC_AGENT_OPTIONS: readonly DelegationCCAgent[] = ["ubuntu_cc"] as const;

/**
 * Selectable statuses; mirrors the ``DelegationStatus`` literal union
 * and the ``ck_delegations_status`` DB CHECK constraint.
 */
const STATUS_OPTIONS: readonly DelegationStatus[] = [
  "pending",
  "running",
  "done",
  "failed",
] as const;

/**
 * Fresh-form defaults for the create mode.
 *
 * ``cc_agent`` and ``status`` mirror the DB ``server_default``
 * (``ubuntu_cc`` and ``pending``); ``started_at`` is left blank so the
 * backend stamps ``NOW()`` via its column-level ``server_default``.
 */
const EMPTY_FORM: DelegationFormState = {
  task_id: "",
  feat_id: "",
  bug_fix_task_id: "",
  bug_id: "",
  cc_agent: "ubuntu_cc",
  prompt: "",
  status: "pending",
  raw_output: "",
  commit_hash: "",
  started_at: "",
  completed_at: "",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(value: DelegationStatus): string {
  switch (value) {
    case "pending":
      return "bg-gray-100 text-gray-800";
    case "running":
      return "bg-amber-100 text-amber-800";
    case "done":
      return "bg-emerald-100 text-emerald-800";
    case "failed":
      return "bg-red-100 text-red-800";
  }
}

/** Tailwind helper for cc-agent pills. */
function ccAgentBadgeClass(value: DelegationCCAgent): string {
  // Currently only ``ubuntu_cc`` exists — the switch keeps the caller
  // exhaustive so new agents (if ever added to ``ck_delegations_cc_agent``)
  // surface as type errors here and can be coloured distinctly.
  switch (value) {
    case "ubuntu_cc":
      return "bg-indigo-100 text-indigo-800";
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

/** Render an optional UUID / string for the detail / list views. */
function formatOptional(value: string | null | undefined): string {
  return value === null || value === undefined || value.length === 0
    ? "—"
    : value;
}

/** Truncate a long multiline string for table display. */
function truncate(value: string, maxLength = 60): string {
  const flattened = value.replace(/\s+/g, " ").trim();
  if (flattened.length <= maxLength) {
    return flattened;
  }
  return `${flattened.slice(0, maxLength - 1)}…`;
}

/**
 * Convert an HTML ``datetime-local`` value (``YYYY-MM-DDTHH:mm``) into
 * the ISO-8601 string the backend expects. Blank → ``null`` (server
 * default / "leave unchanged" on PATCH). The DOM value has no seconds
 * and no timezone; we append ``:00`` to make it a valid ISO-8601
 * timestamp — the backend stores naive timestamps and tolerates the
 * absence of a timezone suffix.
 */
function parseDatetime(raw: string): string | null {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    return null;
  }
  // ``datetime-local`` yields ``YYYY-MM-DDTHH:mm`` — append seconds to
  // form a canonical ISO-8601 timestamp.
  return `${trimmed}:00`;
}

/**
 * Convert an ISO-8601 timestamp into the
 * ``YYYY-MM-DDTHH:mm`` slice consumed by ``<input
 * type="datetime-local">``. Returns ``""`` on blank / bad input.
 */
function formatDatetimeLocal(iso: string | null | undefined): string {
  if (!iso) {
    return "";
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  // Pad to two digits; months are zero-indexed on ``Date``.
  const pad = (value: number) => String(value).padStart(2, "0");
  const year = parsed.getFullYear();
  const month = pad(parsed.getMonth() + 1);
  const day = pad(parsed.getDate());
  const hour = pad(parsed.getHours());
  const minute = pad(parsed.getMinutes());
  return `${year}-${month}-${day}T${hour}:${minute}`;
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

function DelegationAdminPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<DelegationRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [taskFilter, setTaskFilter] = useState("");
  const [featFilter, setFeatFilter] = useState("");
  const [bugFixTaskFilter, setBugFixTaskFilter] = useState("");
  const [bugFilter, setBugFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<DelegationStatus | "">("");
  const [ccAgentFilter, setCcAgentFilter] = useState<DelegationCCAgent | "">(
    "",
  );

  const [detail, setDetail] = useState<DelegationRead | null>(null);
  const [form, setForm] = useState<DelegationFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<DelegationRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            task_id: taskFilter.trim() || undefined,
            feat_id: featFilter.trim() || undefined,
            bug_fix_task_id: bugFixTaskFilter.trim() || undefined,
            bug_id: bugFilter.trim() || undefined,
            // Backend exposes the parameter as ``status`` (alias of
            // ``status_filter`` — see backend/api/routes/delegations.py).
            status: statusFilter || undefined,
            cc_agent: ccAgentFilter || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load delegations.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [
    skip,
    taskFilter,
    featFilter,
    bugFixTaskFilter,
    bugFilter,
    statusFilter,
    ccAgentFilter,
  ]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<DelegationRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load delegation.";
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
        const row = await api.get<DelegationRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          task_id: row.task_id ?? "",
          feat_id: row.feat_id ?? "",
          bug_fix_task_id: row.bug_fix_task_id ?? "",
          bug_id: row.bug_id ?? "",
          cc_agent: row.cc_agent,
          prompt: row.prompt,
          status: row.status,
          raw_output: row.raw_output ?? "",
          commit_hash: row.commit_hash ?? "",
          started_at: formatDatetimeLocal(row.started_at),
          completed_at: formatDatetimeLocal(row.completed_at),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load delegation.";
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
        "Delete this delegation? This is a hard delete. Inbound FKs are handled at the DB level: execution_logs.delegation_id (ON DELETE CASCADE) and guardian_reviews.delegation_id (ON DELETE CASCADE) will be removed alongside this delegation, and auto_fix_attempts.delegation_id (ON DELETE SET NULL) will be silently NULL-ed. Routine operation retains the full delegation history for reporting. Proceed?",
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
        exc instanceof ApiError ? exc.message : "Failed to delete delegation.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const prompt = form.prompt;
      if (prompt.trim().length === 0) {
        throw new Error("Prompt is required.");
      }
      const commit = form.commit_hash.trim();
      const payload: DelegationCreate = {
        task_id: form.task_id.trim() || null,
        feat_id: form.feat_id.trim() || null,
        bug_fix_task_id: form.bug_fix_task_id.trim() || null,
        bug_id: form.bug_id.trim() || null,
        cc_agent: form.cc_agent,
        prompt,
        status: form.status,
        raw_output: form.raw_output.length === 0 ? null : form.raw_output,
        commit_hash: commit.length === 0 ? null : commit,
        started_at: parseDatetime(form.started_at),
        completed_at: parseDatetime(form.completed_at),
      };
      await api.post<DelegationRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to create delegation.";
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
      // Only the mutable lifecycle fields are sent — parent FKs,
      // ``cc_agent`` and ``prompt`` are immutable (see
      // backend/schemas/delegation.py — DelegationUpdate deliberately
      // omits them). Blank values are dropped (service treats them as
      // "leave unchanged").
      const commit = form.commit_hash.trim();
      const startedIso = parseDatetime(form.started_at);
      const completedIso = parseDatetime(form.completed_at);
      const payload: DelegationUpdate = {
        status: form.status,
      };
      if (form.raw_output.length > 0) {
        payload.raw_output = form.raw_output;
      }
      if (commit.length > 0) {
        payload.commit_hash = commit;
      }
      if (startedIso !== null) {
        payload.started_at = startedIso;
      }
      if (completedIso !== null) {
        payload.completed_at = completedIso;
      }
      await api.patch<DelegationRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to update delegation.";
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
          <h2 className="text-xl font-semibold text-gray-900">Delegations</h2>
          <p className="text-sm text-gray-600">
            Per-CC-agent execution records (DESIGN.md §1.18 / §1.7
            ``delegations`` table). A delegation is linked to at most one
            of ``task_id`` / ``feat_id`` / ``bug_fix_task_id`` /
            ``bug_id`` — all four use ``ON DELETE SET NULL`` so the
            delegation row survives deletion of the originating work
            item. Ordered by ``started_at DESC`` (most recent first).
            Delete is a hard delete; inbound FKs
            (``execution_logs.delegation_id`` CASCADE,
            ``guardian_reviews.delegation_id`` CASCADE,
            ``auto_fix_attempts.delegation_id`` SET NULL) are handled
            at the DB level.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new delegation"
          >
            New Delegation
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
        <DelegationList
          items={items}
          total={total}
          isLoading={isLoading}
          taskFilter={taskFilter}
          onTaskFilterChange={(value) => {
            setSkip(0);
            setTaskFilter(value);
          }}
          featFilter={featFilter}
          onFeatFilterChange={(value) => {
            setSkip(0);
            setFeatFilter(value);
          }}
          bugFixTaskFilter={bugFixTaskFilter}
          onBugFixTaskFilterChange={(value) => {
            setSkip(0);
            setBugFixTaskFilter(value);
          }}
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
          ccAgentFilter={ccAgentFilter}
          onCcAgentFilterChange={(value) => {
            setSkip(0);
            setCcAgentFilter(value);
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
        <DelegationDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <DelegationForm
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

interface DelegationListProps {
  items: DelegationRead[];
  total: number;
  isLoading: boolean;
  taskFilter: string;
  onTaskFilterChange: (value: string) => void;
  featFilter: string;
  onFeatFilterChange: (value: string) => void;
  bugFixTaskFilter: string;
  onBugFixTaskFilterChange: (value: string) => void;
  bugFilter: string;
  onBugFilterChange: (value: string) => void;
  statusFilter: DelegationStatus | "";
  onStatusFilterChange: (value: DelegationStatus | "") => void;
  ccAgentFilter: DelegationCCAgent | "";
  onCcAgentFilterChange: (value: DelegationCCAgent | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function DelegationList({
  items,
  total,
  isLoading,
  taskFilter,
  onTaskFilterChange,
  featFilter,
  onFeatFilterChange,
  bugFixTaskFilter,
  onBugFixTaskFilterChange,
  bugFilter,
  onBugFilterChange,
  statusFilter,
  onStatusFilterChange,
  ccAgentFilter,
  onCcAgentFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: DelegationListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
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
            title="Enter a canonical UUID to show delegations for a specific task. Blank = all."
            placeholder="UUID — blank = all"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="feat-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Feat ID
          </label>
          <input
            id="feat-filter"
            type="text"
            value={featFilter}
            onChange={(event) => onFeatFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show delegations for a specific feat. Blank = all."
            placeholder="UUID — blank = all"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="bug-fix-task-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Bug fix task ID
          </label>
          <input
            id="bug-fix-task-filter"
            type="text"
            value={bugFixTaskFilter}
            onChange={(event) => onBugFixTaskFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show delegations for a specific bug fix task. Blank = all."
            placeholder="UUID — blank = all"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

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
            title="Enter a canonical UUID to show delegations for a specific bug. Blank = all."
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
              onStatusFilterChange(event.target.value as DelegationStatus | "")
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
            htmlFor="cc-agent-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            CC agent
          </label>
          <select
            id="cc-agent-filter"
            value={ccAgentFilter}
            onChange={(event) =>
              onCcAgentFilterChange(
                event.target.value as DelegationCCAgent | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {CC_AGENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} delegation{total === 1 ? "" : "s"} total
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
                Agent
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Parent work item (task / feat / bug fix / bug). A delegation is linked to at most one."
              >
                Parent
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Prompt
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
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Delegation ID
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
                  Loading delegations…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No delegations match the current filter.
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
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${ccAgentBadgeClass(item.cc_agent)}`}
                    >
                      {item.cc_agent}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.task_id && <div>task: {item.task_id}</div>}
                    {item.feat_id && <div>feat: {item.feat_id}</div>}
                    {item.bug_fix_task_id && (
                      <div>bug-fix: {item.bug_fix_task_id}</div>
                    )}
                    {item.bug_id && <div>bug: {item.bug_id}</div>}
                    {!item.task_id &&
                      !item.feat_id &&
                      !item.bug_fix_task_id &&
                      !item.bug_id && <span>—</span>}
                  </td>
                  <td
                    className="px-4 py-2 text-sm text-gray-900"
                    title={item.prompt}
                  >
                    {truncate(item.prompt)}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-700">
                    {item.commit_hash ? item.commit_hash.slice(0, 10) : "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.started_at)}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.completed_at)}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.id}
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

interface DelegationDetailProps {
  row: DelegationRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function DelegationDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: DelegationDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading delegation…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Delegation not found.</p>
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
            Delegation ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            CC agent
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${ccAgentBadgeClass(row.cc_agent)}`}
            >
              {row.cc_agent}
            </span>
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
            Task ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.task_id)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Feat ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.feat_id)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Bug fix task ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.bug_fix_task_id)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Bug ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.bug_id)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Prompt
          </dt>
          <dd className="whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-xs text-gray-900">
            {row.prompt}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Raw output
          </dt>
          <dd className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-xs text-gray-900">
            {row.raw_output ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Commit hash
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatOptional(row.commit_hash)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Started at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.started_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Completed at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.completed_at)}
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

interface DelegationFormProps {
  form: DelegationFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: DelegationFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function DelegationForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: DelegationFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<DelegationFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading delegation…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit delegation" : "Create delegation"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
            title="Optional task this delegation executes."
            placeholder="blank = none"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="feat_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Feat ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → feats, ON DELETE SET NULL; immutable
              after create)
            </span>
          </label>
          <input
            id="feat_id"
            type="text"
            value={form.feat_id}
            onChange={(event) => patch({ feat_id: event.target.value })}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Optional feat this delegation executes (feat-level trigger, DESIGN.md §2.6)."
            placeholder="blank = none"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="bug_fix_task_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Bug fix task ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → bug_fix_tasks, ON DELETE SET NULL;
              immutable after create)
            </span>
          </label>
          <input
            id="bug_fix_task_id"
            type="text"
            value={form.bug_fix_task_id}
            onChange={(event) =>
              patch({ bug_fix_task_id: event.target.value })
            }
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Optional bug fix task this delegation executes."
            placeholder="blank = none"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="bug_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Bug ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → bugs, ON DELETE SET NULL; immutable
              after create)
            </span>
          </label>
          <input
            id="bug_id"
            type="text"
            value={form.bug_id}
            onChange={(event) => patch({ bug_id: event.target.value })}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Optional bug this delegation addresses directly."
            placeholder="blank = none"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="cc_agent"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            CC agent
            <span className="ml-1 text-xs font-normal text-gray-500">
              (ubuntu_cc; server default ``ubuntu_cc``; immutable after
              create)
            </span>
          </label>
          <select
            id="cc_agent"
            value={form.cc_agent}
            onChange={(event) =>
              patch({ cc_agent: event.target.value as DelegationCCAgent })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          >
            {CC_AGENT_OPTIONS.map((option) => (
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
            <span className="ml-1 text-xs font-normal text-gray-500">
              (pending | running | done | failed; server default
              ``pending``)
            </span>
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({ status: event.target.value as DelegationStatus })
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
            htmlFor="prompt"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Prompt
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; full CC delegation prompt; immutable after
              create)
            </span>
          </label>
          <textarea
            id="prompt"
            value={form.prompt}
            onChange={(event) => patch({ prompt: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            rows={8}
            minLength={1}
            placeholder="Full CC delegation prompt…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="raw_output"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Raw output
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; NDJSON / text stream captured from the CC
              agent; typically populated as the delegation progresses)
            </span>
          </label>
          <textarea
            id="raw_output"
            value={form.raw_output}
            onChange={(event) => patch({ raw_output: event.target.value })}
            rows={6}
            placeholder="blank = unset"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="commit_hash"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Commit hash
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; up to 40 hex chars)
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

        <div>
          <label
            htmlFor="started_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Started at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; server default ``NOW()`` on create; blank =
              unchanged on edit)
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
              (optional; stamped on done / failed transition; blank =
              unchanged on edit)
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

export default DelegationAdminPage;
