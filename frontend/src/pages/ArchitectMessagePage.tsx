/**
 * ArchitectMessage admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ArchitectMessage CRUD surface against the backend
 * REST router mounted at ``/api/v1/architect-messages`` (see
 * ``backend/api/routes/architect_messages.py``). One ``architect_messages``
 * row is one chat turn inside an :class:`ArchitectSession` — a ``user``
 * prompt or an ``assistant`` reply. See DESIGN.md §1.12
 * ArchitectMessage, §1.5 Architect Sessions / ``architect_messages``
 * table and D-08 SSE streaming.
 *
 * Like the other Feat 6 admin pages (``ArchitectSessionPage``,
 * ``ProjectModulePage``, ``ProjectMemberPage``, ``MigrationIdMapPage``,
 * …) this surface is deliberately self-contained rather than reaching
 * for the global ``architectStore``: per DESIGN.md § 3.3 that store
 * backs the end-user ``ArchitectPage`` chat UI (streaming messages,
 * session state), which is a distinct concern from a per-row
 * administrative CRUD editor. When the store grows dedicated admin
 * actions in a later feat this page can switch over without changing
 * its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``session_id`` and/or
 *     ``role``, with row-level "View", "Edit" and "Delete" actions.
 *   - ``detail`` — read-only view of a single message: primary key,
 *     ``session_id``, ``role``, ``content``, usage columns
 *     (``input_tokens``, ``output_tokens``, ``cost_usd``) and audit
 *     timestamps.
 *   - ``create`` — form that ``POST``s a new message. ``session_id``,
 *     ``role`` and ``content`` are required; the usage/cost columns are
 *     optional (normally ``NULL`` at creation and backfilled after the
 *     SSE stream completes — DESIGN.md §1.5).
 *   - ``edit``   — form that ``PATCH``es the mutable columns
 *     (``input_tokens``, ``output_tokens``, ``cost_usd``).
 *     ``session_id``, ``role`` and ``content`` are rendered read-only —
 *     chat history is **append-only** per DESIGN.md §1.5, so
 *     :class:`ArchitectMessageUpdate` deliberately omits them (see
 *     ``backend/schemas/architect_message.py``).
 *
 * ``DELETE`` is a hard delete. ``architect_messages`` has **no inbound
 * foreign keys** — no other table references it — so no dependency
 * check is required. In normal operation chat history is retained for
 * the lifetime of the session; delete is reserved for test fixtures /
 * admin redaction tooling. The confirmation dialog warns the user.
 * Deleting the parent :class:`ArchitectSession` cascades automatically
 * via ``ON DELETE CASCADE`` on ``session_id`` — that is the usual path
 * for removing a whole conversation.
 *
 * ``cost_usd`` is ``DECIMAL(10, 6)`` on the backend; we transmit it as
 * a plain string on the wire (see ``frontend/src/types/architectMessage.ts``)
 * to preserve the full six-digit decimal precision — a JavaScript
 * ``number`` cannot faithfully round-trip arbitrary decimals. Inputs
 * are captured as strings and blanks are normalised to ``null`` so the
 * column clears cleanly.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / role values / constraint failures to HTTP 422 and
 * they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/architect-messages`` alongside the
 * other Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``). It is
 * distinct from ``ArchitectPage`` (the end-user chat surface at
 * ``/projects/:slug/architect`` and
 * ``/projects/:slug/modules/:code/architect``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  ArchitectMessageCreate,
  ArchitectMessageRead,
  ArchitectMessageRole,
  ArchitectMessageUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the ArchitectMessage router (see backend/main.py). */
const ENDPOINT = "/architect-messages";

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
 * ``select`` / ``number`` input ``value`` is always a string. UUID
 * inputs enforce the canonical shape via the ``pattern`` attribute and
 * the backend rejects malformed values with HTTP 422. The ``role``
 * enum is backed by an ``ArchitectMessageRole`` cast at submit time.
 * ``cost_usd`` is a free-form decimal string (DECIMAL(10,6) on the
 * backend) — see module docstring.
 */
interface ArchitectMessageFormState {
  session_id: string;
  role: ArchitectMessageRole;
  content: string;
  input_tokens: string;
  output_tokens: string;
  cost_usd: string;
}

/**
 * Selectable roles; mirrors the ``ArchitectMessageRole`` literal union
 * and the ``ck_architect_messages_role`` DB CHECK constraint.
 */
const ROLE_OPTIONS: readonly ArchitectMessageRole[] = [
  "user",
  "assistant",
] as const;

/** Fresh-form defaults for the create mode. ``role`` defaults to ``user``
 * — the typical first turn in a new transcript. */
const EMPTY_FORM: ArchitectMessageFormState = {
  session_id: "",
  role: "user",
  content: "",
  input_tokens: "",
  output_tokens: "",
  cost_usd: "",
};

/** Tailwind helper for role pills. */
function roleBadgeClass(value: ArchitectMessageRole): string {
  switch (value) {
    case "user":
      return "bg-sky-100 text-sky-800";
    case "assistant":
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

/**
 * Parse an ``input type="number"`` string into an integer or ``null``.
 *
 * Returns ``null`` for blank input so the column clears cleanly on
 * PATCH. Non-integer or negative inputs fall through to ``null`` too —
 * the backend ``Field(ge=0)`` would reject them with HTTP 422 but we
 * prefer a clean client-side round-trip. The HTML ``min`` / ``step``
 * attributes catch the common typos before submit.
 */
function parseOptionalNonNegativeInt(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const parsed = Number.parseInt(trimmed, 10);
  if (Number.isNaN(parsed) || parsed < 0) {
    return null;
  }
  return parsed;
}

/**
 * Normalise a decimal string for ``cost_usd``.
 *
 * Returns ``null`` for blank input (clears the column). Otherwise
 * returns the trimmed string as-is — the backend parses it into a
 * ``Decimal`` and rejects malformed values with HTTP 422. We
 * deliberately do NOT round-trip through ``Number`` because
 * ``cost_usd`` is ``DECIMAL(10, 6)`` and ``Number`` cannot faithfully
 * represent arbitrary decimals.
 */
function parseOptionalDecimal(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed;
}

/**
 * Render an integer (or ``null``) for the detail / list views.
 * Matches the "—" convention used elsewhere in Feat 6 for missing values.
 */
function formatOptionalInt(value: number | null): string {
  return value === null || value === undefined ? "—" : String(value);
}

/**
 * Render the ``cost_usd`` string for the detail / list views. The
 * backend transmits ``cost_usd`` as a plain string to preserve
 * ``DECIMAL(10, 6)`` precision; we show it verbatim with a ``$``
 * prefix so the admin can read raw billing values at a glance.
 */
function formatCostUsd(value: string | null): string {
  if (value === null || value === undefined || value.length === 0) {
    return "—";
  }
  return `$${value}`;
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
 * HTML ``pattern`` expression for a ``DECIMAL(10, 6)`` value:
 * up to 10 total digits, up to 6 after the decimal point. Optional
 * leading sign not permitted — costs are non-negative. Rendered on
 * the ``cost_usd`` input so the browser rejects egregious typos before
 * submit; the backend is still the authoritative validator.
 */
const DECIMAL_10_6_PATTERN = "\\d{1,4}(\\.\\d{1,6})?";

function ArchitectMessagePage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ArchitectMessageRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [sessionFilter, setSessionFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState<ArchitectMessageRole | "">("");

  const [detail, setDetail] = useState<ArchitectMessageRead | null>(null);
  const [form, setForm] = useState<ArchitectMessageFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ArchitectMessageRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            session_id: sessionFilter.trim() || undefined,
            role: roleFilter || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load Architect messages.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, sessionFilter, roleFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ArchitectMessageRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load Architect message.";
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
        const row = await api.get<ArchitectMessageRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          session_id: row.session_id,
          role: row.role,
          content: row.content,
          input_tokens:
            row.input_tokens === null || row.input_tokens === undefined
              ? ""
              : String(row.input_tokens),
          output_tokens:
            row.output_tokens === null || row.output_tokens === undefined
              ? ""
              : String(row.output_tokens),
          cost_usd: row.cost_usd ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load Architect message.";
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
        "Delete this Architect message? This is a hard delete — no other tables reference architect_messages, so no dependency check applies. Chat history is normally retained for the lifetime of the session; delete is reserved for redaction / test-fixture cleanup.",
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
          : "Failed to delete Architect message.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: ArchitectMessageCreate = {
        session_id: form.session_id.trim(),
        role: form.role,
        content: form.content,
        input_tokens: parseOptionalNonNegativeInt(form.input_tokens),
        output_tokens: parseOptionalNonNegativeInt(form.output_tokens),
        cost_usd: parseOptionalDecimal(form.cost_usd),
      };
      await api.post<ArchitectMessageRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create Architect message.";
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
      // ``session_id``, ``role`` and ``content`` are immutable — chat
      // history is append-only (DESIGN.md §1.5). Only the usage / cost
      // columns are sent; blanks are normalised to ``null`` so the
      // column clears cleanly on backfill corrections.
      const payload: ArchitectMessageUpdate = {
        input_tokens: parseOptionalNonNegativeInt(form.input_tokens),
        output_tokens: parseOptionalNonNegativeInt(form.output_tokens),
        cost_usd: parseOptionalDecimal(form.cost_usd),
      };
      await api.patch<ArchitectMessageRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update Architect message.";
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
            Architect messages
          </h2>
          <p className="text-sm text-gray-600">
            Architect chat turns — one row per ``user`` prompt or
            ``assistant`` reply inside an Architect session (DESIGN.md
            §1.12 / §1.5). Chat history is append-only: ``session_id``,
            ``role`` and ``content`` are immutable; only the usage / cost
            columns (``input_tokens``, ``output_tokens``, ``cost_usd``)
            remain editable so token counts can be backfilled after the
            SSE stream completes.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new Architect message"
          >
            New Message
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
        <ArchitectMessageList
          items={items}
          total={total}
          isLoading={isLoading}
          sessionFilter={sessionFilter}
          onSessionFilterChange={(value) => {
            setSkip(0);
            setSessionFilter(value);
          }}
          roleFilter={roleFilter}
          onRoleFilterChange={(value) => {
            setSkip(0);
            setRoleFilter(value);
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
        <ArchitectMessageDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ArchitectMessageForm
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

interface ArchitectMessageListProps {
  items: ArchitectMessageRead[];
  total: number;
  isLoading: boolean;
  sessionFilter: string;
  onSessionFilterChange: (value: string) => void;
  roleFilter: ArchitectMessageRole | "";
  onRoleFilterChange: (value: ArchitectMessageRole | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ArchitectMessageList({
  items,
  total,
  isLoading,
  sessionFilter,
  onSessionFilterChange,
  roleFilter,
  onRoleFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ArchitectMessageListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="session-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Session ID
          </label>
          <input
            id="session-filter"
            type="text"
            value={sessionFilter}
            onChange={(event) => onSessionFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID, or leave blank to show messages across all sessions."
            placeholder="UUID — blank = all sessions"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="role-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Role
          </label>
          <select
            id="role-filter"
            value={roleFilter}
            onChange={(event) =>
              onRoleFilterChange(
                event.target.value as ArchitectMessageRole | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {ROLE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} message{total === 1 ? "" : "s"} total
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
                Message
              </th>
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
                Role
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Content
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Tokens (in / out)
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Cost
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
                  Loading Architect messages…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No Architect messages match the current filter.
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
                    {item.session_id}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${roleBadgeClass(item.role)}`}
                    >
                      {item.role}
                    </span>
                  </td>
                  <td className="max-w-sm truncate px-4 py-2 text-sm text-gray-700">
                    <span title={item.content}>{item.content}</span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    {formatOptionalInt(item.input_tokens)}
                    {" / "}
                    {formatOptionalInt(item.output_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    {formatCostUsd(item.cost_usd)}
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

interface ArchitectMessageDetailProps {
  row: ArchitectMessageRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ArchitectMessageDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ArchitectMessageDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading Architect message…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Architect message not found.</p>
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
            Message ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Role
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${roleBadgeClass(row.role)}`}
            >
              {row.role}
            </span>
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Session ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.session_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Content
          </dt>
          <dd className="whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-900">
            {row.content}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Input tokens
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatOptionalInt(row.input_tokens)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Output tokens
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatOptionalInt(row.output_tokens)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Cost (USD)
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatCostUsd(row.cost_usd)}
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

interface ArchitectMessageFormProps {
  form: ArchitectMessageFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ArchitectMessageFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ArchitectMessageForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ArchitectMessageFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ArchitectMessageFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading Architect message…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit Architect message" : "Create Architect message"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="session_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Session ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → architect_sessions, ON DELETE CASCADE;
              immutable after create)
            </span>
          </label>
          <input
            id="session_id"
            type="text"
            value={form.session_id}
            onChange={(event) => patch({ session_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the Architect session UUID this message belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="role"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Role
            <span className="ml-1 text-xs font-normal text-gray-500">
              (immutable after create)
            </span>
          </label>
          <select
            id="role"
            value={form.role}
            onChange={(event) =>
              patch({
                role: event.target.value as ArchitectMessageRole,
              })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          >
            {ROLE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="content"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Content
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; immutable after create — chat history is
              append-only)
            </span>
          </label>
          <textarea
            id="content"
            value={form.content}
            onChange={(event) => patch({ content: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            rows={6}
            minLength={1}
            placeholder="Full message body…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="input_tokens"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Input tokens
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; backfilled after the SSE stream completes)
            </span>
          </label>
          <input
            id="input_tokens"
            type="number"
            min={0}
            step={1}
            value={form.input_tokens}
            onChange={(event) => patch({ input_tokens: event.target.value })}
            placeholder="e.g. 1234"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="output_tokens"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Output tokens
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; backfilled after the SSE stream completes)
            </span>
          </label>
          <input
            id="output_tokens"
            type="number"
            min={0}
            step={1}
            value={form.output_tokens}
            onChange={(event) => patch({ output_tokens: event.target.value })}
            placeholder="e.g. 789"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="cost_usd"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Cost (USD)
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; DECIMAL(10, 6) — up to 4 integer digits and 6
              fractional digits. Transmitted as a string to preserve
              precision.)
            </span>
          </label>
          <input
            id="cost_usd"
            type="text"
            inputMode="decimal"
            value={form.cost_usd}
            onChange={(event) => patch({ cost_usd: event.target.value })}
            pattern={DECIMAL_10_6_PATTERN}
            title="Non-negative decimal, up to 4 integer digits and 6 fractional digits."
            placeholder="e.g. 0.012345"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
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

export default ArchitectMessagePage;
