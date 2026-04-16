/**
 * AutoFixAttempt admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 AutoFixAttempt CRUD surface against the backend REST
 * router mounted at ``/api/v1/auto-fix-attempts`` (see
 * ``backend/api/routes/auto_fix_attempts.py``). One ``auto_fix_attempts``
 * row is one Guardian auto-fix retry of a failed feat delegation —
 * DESIGN.md §1.20 AutoFixAttempt / §2 ``auto_fix_attempts`` table — and
 * the raw data behind the ``GuardianPanel`` / ``DelegationStatus`` UI
 * (DESIGN.md §3.1).
 *
 * Like the other Feat 6 admin pages (``FeatPage``, ``EpicPage``,
 * ``ArchitectMessagePage``, ``DesignDocumentPage``, …) this surface is
 * deliberately self-contained rather than reaching for the global
 * ``delegationStore``: per DESIGN.md §3.3 that store backs the end-user
 * ``DelegationPage`` live-output UI (CC streaming, Guardian layer
 * results), which is a distinct concern from a per-row administrative
 * CRUD editor. When the store grows dedicated admin actions in a later
 * feat this page can switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``feat_id`` and/or
 *     ``delegation_id``, with row-level "View", "Edit" and "Delete"
 *     actions. Results are ordered by ``attempt_number ASC`` (attempt 1,
 *     attempt 2, …) — matching the service-layer ordering owned by
 *     ``backend/services/auto_fix_attempt.py`` and the retry-numbering
 *     convention used throughout the Tasks / Delegation UI.
 *   - ``detail`` — read-only view of a single attempt: primary key,
 *     ``feat_id``, ``attempt_number``, full ``error_description`` /
 *     ``fix_description``, ``delegation_id`` and audit timestamps.
 *   - ``create`` — form that ``POST``s a new attempt. ``feat_id`` and
 *     ``error_description`` are required; ``fix_description`` and
 *     ``delegation_id`` are optional (normally ``NULL`` at creation and
 *     backfilled via PATCH once the fix delegation is spawned /
 *     completes — DESIGN.md §1.20). ``attempt_number`` is auto-assigned
 *     by the service layer as ``MAX(attempt_number) + 1`` per feat —
 *     never sent by the client. Concurrent-create races on the same
 *     feat surface as HTTP 409 via the DB-level ``UNIQUE(feat_id,
 *     attempt_number)`` constraint
 *     (``uq_auto_fix_attempts_feat_id_attempt_number``) and are shown
 *     verbatim in the inline error banner.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``error_description``, ``fix_description``, ``delegation_id``).
 *     ``feat_id`` and ``attempt_number`` are rendered read-only — the
 *     attempt identity and its position within the feat's retry
 *     sequence must not be rewritten after the fact
 *     (:class:`AutoFixAttemptUpdate` deliberately omits both, see
 *     ``backend/schemas/auto_fix_attempt.py``). PATCH semantics: fields
 *     that are blank / ``null`` are treated as "leave unchanged" by the
 *     service; the explicit-null transitions
 *     ``fix_description -> NULL`` / ``delegation_id -> NULL`` are not
 *     expressible through the admin UI — ``delegation_id`` already
 *     clears automatically on delegation deletion via ``ON DELETE SET
 *     NULL``, and ``fix_description`` corrections belong to admin
 *     tooling.
 *
 * ``DELETE`` is a hard delete. ``auto_fix_attempts`` has no inbound
 * foreign keys — no other table references it — so no RESTRICT
 * dependency check is required. Routine operation retains the full
 * retry history for reporting (DESIGN.md §1.20); delete is reserved for
 * test-fixture cleanup and admin redaction. The confirmation dialog
 * warns the user. Deleting the parent ``feats`` row cascades
 * automatically via ``auto_fix_attempts.feat_id ON DELETE CASCADE`` —
 * that is the usual path for wiping a whole retry history.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid foreign
 * keys / constraint failures to HTTP 422 and they are shown verbatim in
 * the inline error banner.
 *
 * This page sits under ``/admin/auto-fix-attempts`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``,
 * ``/admin/architect-messages``, ``/admin/design-documents``,
 * ``/admin/epics``, ``/admin/feats``). It is distinct from the end-user
 * ``GuardianPanel`` / ``DelegationStatus`` surfaces on the Delegation
 * page (DESIGN.md §3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  AutoFixAttemptCreate,
  AutoFixAttemptRead,
  AutoFixAttemptUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the AutoFixAttempt router (see backend/main.py). */
const ENDPOINT = "/auto-fix-attempts";

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
 * ``textarea`` input ``value`` is always a string. UUID inputs enforce
 * the canonical shape via the ``pattern`` attribute and the backend
 * rejects malformed values with HTTP 422.
 */
interface AutoFixAttemptFormState {
  feat_id: string;
  error_description: string;
  fix_description: string;
  delegation_id: string;
}

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: AutoFixAttemptFormState = {
  feat_id: "",
  error_description: "",
  fix_description: "",
  delegation_id: "",
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

/** Render an optional UUID / string for the detail / list views. */
function formatOptional(value: string | null | undefined): string {
  return value === null || value === undefined || value.length === 0
    ? "—"
    : value;
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

function AutoFixAttemptPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<AutoFixAttemptRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [featFilter, setFeatFilter] = useState("");
  const [delegationFilter, setDelegationFilter] = useState("");

  const [detail, setDetail] = useState<AutoFixAttemptRead | null>(null);
  const [form, setForm] = useState<AutoFixAttemptFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<AutoFixAttemptRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            feat_id: featFilter.trim() || undefined,
            delegation_id: delegationFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load auto-fix attempts.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, featFilter, delegationFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<AutoFixAttemptRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load auto-fix attempt.";
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
        const row = await api.get<AutoFixAttemptRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          feat_id: row.feat_id,
          error_description: row.error_description,
          fix_description: row.fix_description ?? "",
          delegation_id: row.delegation_id ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load auto-fix attempt.";
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
        "Delete this auto-fix attempt? This is a hard delete. auto_fix_attempts has no inbound foreign keys — no dependency check applies. Routine operation retains the full retry history for reporting; delete is reserved for redaction / test-fixture cleanup. Deleting the parent feat cascades automatically via ON DELETE CASCADE — use that path to wipe an entire retry history.",
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
          : "Failed to delete auto-fix attempt.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      // ``attempt_number`` is auto-assigned by the service layer as
      // ``MAX(attempt_number) + 1`` per feat — never sent by the client.
      const fixDescription = form.fix_description.trim();
      const delegationId = form.delegation_id.trim();
      const payload: AutoFixAttemptCreate = {
        feat_id: form.feat_id.trim(),
        error_description: form.error_description,
        fix_description: fixDescription.length === 0 ? null : fixDescription,
        delegation_id: delegationId.length === 0 ? null : delegationId,
      };
      await api.post<AutoFixAttemptRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create auto-fix attempt.";
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
      // ``feat_id`` and ``attempt_number`` are immutable (see
      // backend/schemas/auto_fix_attempt.py — AutoFixAttemptUpdate
      // deliberately omits both).
      //
      // PATCH semantics: the service treats ``None`` as "leave
      // unchanged", so the explicit-null transitions
      // ``fix_description -> NULL`` / ``delegation_id -> NULL`` are not
      // expressible here — blanks are therefore dropped from the
      // payload rather than sent as ``null``. ``delegation_id`` already
      // clears automatically on delegation deletion via ``ON DELETE
      // SET NULL``.
      const fixDescription = form.fix_description.trim();
      const delegationId = form.delegation_id.trim();
      const payload: AutoFixAttemptUpdate = {
        error_description: form.error_description,
      };
      if (fixDescription.length > 0) {
        payload.fix_description = fixDescription;
      }
      if (delegationId.length > 0) {
        payload.delegation_id = delegationId;
      }
      await api.patch<AutoFixAttemptRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update auto-fix attempt.";
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
            Auto-fix attempts
          </h2>
          <p className="text-sm text-gray-600">
            Guardian auto-fix retries — one row per retry of a failed
            feat delegation (DESIGN.md §1.20 / §2 ``auto_fix_attempts``
            table). Ordered by ``attempt_number`` ASC (attempt 1, attempt
            2, …) — auto-assigned per feat at create time. Delete is a
            hard delete; ``auto_fix_attempts`` has no inbound FKs, so no
            dependency check applies.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new auto-fix attempt"
          >
            New Attempt
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
        <AutoFixAttemptList
          items={items}
          total={total}
          isLoading={isLoading}
          featFilter={featFilter}
          onFeatFilterChange={(value) => {
            setSkip(0);
            setFeatFilter(value);
          }}
          delegationFilter={delegationFilter}
          onDelegationFilterChange={(value) => {
            setSkip(0);
            setDelegationFilter(value);
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
        <AutoFixAttemptDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <AutoFixAttemptForm
          form={form}
          mode={mode.kind}
          isSaving={isSaving}
          isLoading={isLoading && mode.kind === "edit"}
          editingAttemptNumber={
            mode.kind === "edit"
              ? (items.find((item) => item.id === mode.id)?.attempt_number ??
                null)
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

interface AutoFixAttemptListProps {
  items: AutoFixAttemptRead[];
  total: number;
  isLoading: boolean;
  featFilter: string;
  onFeatFilterChange: (value: string) => void;
  delegationFilter: string;
  onDelegationFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function AutoFixAttemptList({
  items,
  total,
  isLoading,
  featFilter,
  onFeatFilterChange,
  delegationFilter,
  onDelegationFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: AutoFixAttemptListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
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
            title="Enter a canonical UUID to show attempts under a specific feat. Blank = all feats."
            placeholder="UUID — blank = all feats"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

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
            title="Reverse lookup — find the attempt that spawned a specific auto-fix delegation. Blank = all delegations."
            placeholder="UUID — blank = all delegations"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} attempt{total === 1 ? "" : "s"} total
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Stable retry number auto-assigned per feat (MAX + 1)."
              >
                #
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Feat
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Error
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Fix
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
                Attempt ID
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
                  Loading auto-fix attempts…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No auto-fix attempts match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    #{item.attempt_number}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.feat_id}
                  </td>
                  <td className="max-w-xs truncate px-4 py-2 text-sm text-gray-700">
                    <span title={item.error_description}>
                      {item.error_description}
                    </span>
                  </td>
                  <td className="max-w-xs truncate px-4 py-2 text-sm text-gray-700">
                    <span title={item.fix_description ?? ""}>
                      {formatOptional(item.fix_description)}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {formatOptional(item.delegation_id)}
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

interface AutoFixAttemptDetailProps {
  row: AutoFixAttemptRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function AutoFixAttemptDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: AutoFixAttemptDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading auto-fix attempt…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Auto-fix attempt not found.</p>
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
            Attempt ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Attempt number
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            #{row.attempt_number}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Feat ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.feat_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Error description
          </dt>
          <dd className="whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-900">
            {row.error_description}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Fix description
          </dt>
          <dd className="whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-900">
            {row.fix_description === null ||
            row.fix_description === undefined ||
            row.fix_description.length === 0
              ? "—"
              : row.fix_description}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Delegation ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.delegation_id)}
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

interface AutoFixAttemptFormProps {
  form: AutoFixAttemptFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  /** Stable retry number of the row being edited, when known. */
  editingAttemptNumber: number | null;
  onChange: (form: AutoFixAttemptFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function AutoFixAttemptForm({
  form,
  mode,
  isSaving,
  isLoading,
  editingAttemptNumber,
  onChange,
  onCancel,
  onSubmit,
}: AutoFixAttemptFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<AutoFixAttemptFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading auto-fix attempt…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit auto-fix attempt" : "Create auto-fix attempt"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="feat_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Feat ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → feats, ON DELETE CASCADE; immutable after create)
            </span>
          </label>
          <input
            id="feat_id"
            type="text"
            value={form.feat_id}
            onChange={(event) => patch({ feat_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the feat UUID this auto-fix attempt belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        {isEdit && editingAttemptNumber !== null && (
          <div className="sm:col-span-2">
            <label
              htmlFor="attempt_number"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Attempt number
              <span className="ml-1 text-xs font-normal text-gray-500">
                (auto-assigned per feat; immutable)
              </span>
            </label>
            <input
              id="attempt_number"
              type="text"
              value={`#${editingAttemptNumber}`}
              readOnly
              className="block w-full rounded-md border border-gray-300 bg-gray-100 px-3 py-2 font-mono text-sm text-gray-500 shadow-sm"
            />
          </div>
        )}

        <div className="sm:col-span-2">
          <label
            htmlFor="error_description"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Error description
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; accumulated error context from the failed
              delegation)
            </span>
          </label>
          <textarea
            id="error_description"
            value={form.error_description}
            onChange={(event) =>
              patch({ error_description: event.target.value })
            }
            required
            minLength={1}
            rows={6}
            placeholder="Stack trace, test failure output, Guardian findings…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="fix_description"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Fix description
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; typically populated once the fix delegation
              completes — leave blank at create time)
            </span>
          </label>
          <textarea
            id="fix_description"
            value={form.fix_description}
            onChange={(event) =>
              patch({ fix_description: event.target.value })
            }
            rows={4}
            placeholder="Remediation summary…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="delegation_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Delegation ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → delegations, ON DELETE SET NULL —
              clears automatically if the delegation is later deleted)
            </span>
          </label>
          <input
            id="delegation_id"
            type="text"
            value={form.delegation_id}
            onChange={(event) => patch({ delegation_id: event.target.value })}
            pattern={UUID_PATTERN}
            title="Optional UUID of the auto-fix delegation spawned for this attempt."
            placeholder="blank = not yet spawned"
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

export default AutoFixAttemptPage;
