/**
 * UserSession admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 UserSession CRUD surface against the backend REST
 * router mounted at ``/api/v1/user-sessions`` (see
 * ``backend/api/routes/user_sessions.py``). One ``user_sessions`` row
 * represents a per-user JWT lifecycle anchor — DESIGN.md §1.1 "Auth
 * pattern" / §2 ``user_sessions`` table — whose ``token_version``
 * column is bumped on logout to invalidate every outstanding JWT
 * issued against the session (the ``tv`` claim is verified against
 * this column on every authenticated request).
 *
 * Like the other Feat 6 admin pages (``UserPage``, ``FeatPage``,
 * ``AutoFixAttemptPage``, …) this surface is deliberately
 * self-contained rather than reaching for a Zustand store: DESIGN.md
 * §3.3 defines an ``authStore`` that only tracks the currently
 * authenticated user's own JWT — not the full session registry.  When
 * a global store grows dedicated admin actions in a later feat this
 * page can switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``user_id`` with
 *     row-level "View", "Edit" and "Delete" actions.  Results are
 *     ordered by ``created_at DESC`` — the "most recent sessions
 *     first" convention that matches the Settings page "Active
 *     sessions" UI (DESIGN.md §3.1).
 *   - ``detail`` — read-only view of a single session: primary key,
 *     ``user_id``, ``token_version``, ``last_seen_at`` and the audit
 *     timestamps.
 *   - ``create`` — form that ``POST``s a new session.  ``user_id`` is
 *     required; ``token_version`` defaults to ``0`` to mirror the DB
 *     ``server_default``; ``last_seen_at`` is optional — leaving it
 *     blank defers to the DB-level ``NOW()`` default (DESIGN.md
 *     §1.1).  Explicit back-dating is supported for import / fixture
 *     flows.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``token_version``, ``last_seen_at``).  ``user_id`` is rendered
 *     read-only — a session belongs to exactly one user for its
 *     lifetime, so the FK is immutable
 *     (:class:`UserSessionUpdate` in ``backend/schemas/user_session.py``
 *     deliberately omits it).  PATCH semantics: fields that are blank
 *     on the form are dropped from the payload and left unchanged by
 *     the service.
 *
 * ``DELETE`` is a hard delete.  ``user_sessions`` has no inbound
 * foreign keys — no other table references it — so no RESTRICT
 * dependency check applies.  This is the canonical "logout" /
 * "session expired" cleanup path.  Deleting the parent ``users`` row
 * cascades automatically via ``user_sessions.user_id ON DELETE
 * CASCADE`` (see DESIGN.md §2) — that is the usual "wipe every
 * session for this account" path.  The confirmation dialog warns the
 * operator about the irreversible rotation effect.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.  The backend maps invalid
 * foreign keys / constraint failures to HTTP 422 and they are shown
 * verbatim in the inline error banner.
 *
 * This page sits under ``/admin/user-sessions`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``,
 * ``/admin/architect-messages``, ``/admin/design-documents``,
 * ``/admin/epics``, ``/admin/feats``, ``/admin/tasks``,
 * ``/admin/auto-fix-attempts``, ``/admin/kb-documents``,
 * ``/admin/module-dependencies``, ``/admin/raw-specifications``,
 * ``/admin/professional-specifications``, ``/admin/report-configs``,
 * ``/admin/delegations``, ``/admin/execution-logs``,
 * ``/admin/guardian-reviews``).  It is distinct from the end-user
 * SettingsPage "Active sessions" surface (DESIGN.md §3.1) which lets
 * the signed-in user manage their own sessions.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  UserSessionCreate,
  UserSessionRead,
  UserSessionUpdate,
} from "../types";

/** REST prefix for the UserSession router (see backend/main.py). */
const ENDPOINT = "/user-sessions";

/** Page size used by the list view.  Matches the backend default (capped at 100). */
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
 * ``datetime-local`` input ``value`` is always a string.  UUID inputs
 * enforce the canonical shape via the ``pattern`` attribute and the
 * backend rejects malformed values with HTTP 422.  The
 * ``token_version`` input is captured as a string here and coerced to
 * a non-negative integer at submit time — an empty string on edit
 * means "leave unchanged".
 */
interface UserSessionFormState {
  user_id: string;
  token_version: string;
  last_seen_at: string;
}

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: UserSessionFormState = {
  user_id: "",
  token_version: "0",
  last_seen_at: "",
};

/**
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend).  Rendered on UUID inputs
 * so obvious typos are caught by the browser's constraint-validation
 * API before the form is submitted — the backend would otherwise
 * reject them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** Format an ISO timestamp as a locale date-time string, tolerant of bad input. */
function formatTimestamp(iso: string | null | undefined): string {
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
 * Convert an ISO timestamp (e.g. ``2026-04-16T09:12:34.567Z``) to the
 * ``YYYY-MM-DDTHH:MM`` shape accepted by ``<input type="datetime-local">``.
 * Returns an empty string for falsy input so the edit form renders a
 * blank field.
 */
function toLocalInputValue(iso: string | null | undefined): string {
  if (!iso) {
    return "";
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  // Offset to local time, then trim seconds / trailing Z — the
  // datetime-local input ignores anything past the minute.
  const local = new Date(
    parsed.getTime() - parsed.getTimezoneOffset() * 60_000,
  );
  return local.toISOString().slice(0, 16);
}

function UserSessionPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<UserSessionRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [userFilter, setUserFilter] = useState("");

  const [detail, setDetail] = useState<UserSessionRead | null>(null);
  const [form, setForm] = useState<UserSessionFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<UserSessionRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            user_id: userFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load user sessions.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, userFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<UserSessionRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load user session.";
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
        const row = await api.get<UserSessionRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          user_id: row.user_id,
          token_version: String(row.token_version),
          last_seen_at: toLocalInputValue(row.last_seen_at),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load user session.";
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
        "Delete this user session? This is a hard delete and invalidates every outstanding JWT issued against it. ``user_sessions`` has no inbound FKs, so no dependency check applies. Deleting the parent user cascades automatically via ON DELETE CASCADE — use that path to wipe every session for an account.",
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
          : "Failed to delete user session.";
      setError(message);
    }
  };

  /**
   * Parse the ``token_version`` form field into a non-negative integer.
   * Returns ``null`` when the input is blank (edit mode "leave
   * unchanged") or when the value is not a valid non-negative integer
   * — in which case the caller surfaces a validation error.
   */
  const parseTokenVersion = (raw: string): number | null => {
    const trimmed = raw.trim();
    if (trimmed.length === 0) {
      return null;
    }
    if (!/^\d+$/.test(trimmed)) {
      return null;
    }
    const parsed = Number.parseInt(trimmed, 10);
    if (Number.isNaN(parsed) || parsed < 0) {
      return null;
    }
    return parsed;
  };

  /**
   * Convert a ``datetime-local`` value (``YYYY-MM-DDTHH:MM``) into a
   * full ISO-8601 UTC string suitable for the backend.  Returns an
   * empty string when the input is blank — the caller drops the field
   * from the payload so the DB ``server_default=NOW()`` kicks in (on
   * create) or the service "leave unchanged" path is taken (on edit).
   */
  const parseLocalDatetime = (raw: string): string => {
    const trimmed = raw.trim();
    if (trimmed.length === 0) {
      return "";
    }
    const parsed = new Date(trimmed);
    if (Number.isNaN(parsed.getTime())) {
      return "";
    }
    return parsed.toISOString();
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const tokenVersion = parseTokenVersion(form.token_version);
      if (tokenVersion === null) {
        setError("Token version must be a non-negative integer.");
        setIsSaving(false);
        return;
      }
      const isoLastSeen = parseLocalDatetime(form.last_seen_at);
      const payload: UserSessionCreate = {
        user_id: form.user_id.trim(),
        token_version: tokenVersion,
      };
      if (isoLastSeen.length > 0) {
        payload.last_seen_at = isoLastSeen;
      }
      await api.post<UserSessionRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create user session.";
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
      // ``user_id`` is immutable (see backend/schemas/user_session.py —
      // UserSessionUpdate deliberately omits it).
      //
      // PATCH semantics: the service treats ``None`` as "leave
      // unchanged", so blanks are dropped from the payload rather than
      // sent as ``null``.  The explicit-null transitions are not
      // meaningful for this entity — ``token_version`` is NOT NULL at
      // the DB level with ``server_default='0'`` and ``last_seen_at``
      // is NOT NULL with ``server_default=NOW()``.
      const payload: UserSessionUpdate = {};
      const trimmedTv = form.token_version.trim();
      if (trimmedTv.length > 0) {
        const tokenVersion = parseTokenVersion(form.token_version);
        if (tokenVersion === null) {
          setError("Token version must be a non-negative integer.");
          setIsSaving(false);
          return;
        }
        payload.token_version = tokenVersion;
      }
      const trimmedLastSeen = form.last_seen_at.trim();
      if (trimmedLastSeen.length > 0) {
        const isoLastSeen = parseLocalDatetime(form.last_seen_at);
        if (isoLastSeen.length === 0) {
          setError("Last seen timestamp is not a valid date / time.");
          setIsSaving(false);
          return;
        }
        payload.last_seen_at = isoLastSeen;
      }
      await api.patch<UserSessionRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update user session.";
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
            User sessions
          </h2>
          <p className="text-sm text-gray-600">
            Per-user JWT lifecycle anchors — one row per active session
            (DESIGN.md §1.1 "Auth pattern" / §2 ``user_sessions``
            table). Ordered by ``created_at`` DESC (most recent first).
            Bumping ``token_version`` invalidates every outstanding JWT
            issued against the session; deleting a row is the canonical
            "logout" / "session expired" cleanup path.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new user session"
          >
            New Session
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
        <UserSessionList
          items={items}
          total={total}
          isLoading={isLoading}
          userFilter={userFilter}
          onUserFilterChange={(value) => {
            setSkip(0);
            setUserFilter(value);
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
        <UserSessionDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <UserSessionForm
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

interface UserSessionListProps {
  items: UserSessionRead[];
  total: number;
  isLoading: boolean;
  userFilter: string;
  onUserFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function UserSessionList({
  items,
  total,
  isLoading,
  userFilter,
  onUserFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: UserSessionListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="user-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            User ID
          </label>
          <input
            id="user-filter"
            type="text"
            value={userFilter}
            onChange={(event) => onUserFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show every session for a specific user. Blank = all users."
            placeholder="UUID — blank = all users"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} session{total === 1 ? "" : "s"} total
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
                Session ID
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                User
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Monotonically increasing JWT invalidation counter."
              >
                tv
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Last seen
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
                  colSpan={6}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading user sessions…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No user sessions match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.id}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.user_id}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    {item.token_version}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.last_seen_at)}
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

interface UserSessionDetailProps {
  row: UserSessionRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function UserSessionDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: UserSessionDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading user session…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">User session not found.</p>
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
            Session ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            User ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.user_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Token version
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {row.token_version}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Last seen at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.last_seen_at)}
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

interface UserSessionFormProps {
  form: UserSessionFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: UserSessionFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function UserSessionForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: UserSessionFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<UserSessionFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading user session…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit user session" : "Create user session"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="user_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            User ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → users, ON DELETE CASCADE; immutable after
              create)
            </span>
          </label>
          <input
            id="user_id"
            type="text"
            value={form.user_id}
            onChange={(event) => patch({ user_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the user UUID this session belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="token_version"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Token version
            <span className="ml-1 text-xs font-normal text-gray-500">
              {isEdit
                ? "(leave blank to keep unchanged; bump to invalidate all outstanding JWTs)"
                : "(defaults to 0; DB server_default = '0')"}
            </span>
          </label>
          <input
            id="token_version"
            type="number"
            value={form.token_version}
            onChange={(event) =>
              patch({ token_version: event.target.value })
            }
            min={0}
            step={1}
            inputMode="numeric"
            required={!isEdit}
            placeholder="0"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="last_seen_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Last seen at
            <span className="ml-1 text-xs font-normal text-gray-500">
              {isEdit
                ? "(leave blank to keep unchanged)"
                : "(optional; blank defers to the DB NOW() default)"}
            </span>
          </label>
          <input
            id="last_seen_at"
            type="datetime-local"
            value={form.last_seen_at}
            onChange={(event) =>
              patch({ last_seen_at: event.target.value })
            }
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

export default UserSessionPage;
