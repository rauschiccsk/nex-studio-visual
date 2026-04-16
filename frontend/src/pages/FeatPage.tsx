/**
 * Feat admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 Feat CRUD surface against the backend REST router
 * mounted at ``/api/v1/feats`` (see ``backend/api/routes/feats.py``).
 * A ``feats`` row is the middle level of the Epic → Feat → Task task
 * hierarchy (DESIGN.md §1.9 Tasks hierarchy, §2 ``feats`` table) and
 * backs the end-user ``TasksPage`` / ``FeatCard`` UI (DESIGN.md §3.1).
 *
 * Like the other Feat 6 admin pages (``EpicPage``,
 * ``DesignDocumentPage``, ``ArchitectMessagePage``,
 * ``ProjectModulePage``, …) this surface is deliberately self-contained
 * rather than reaching for a global Zustand store: per DESIGN.md §3.3
 * ``taskStore`` backs the end-user ``EpicList`` / ``FeatCard`` /
 * ``TaskItem`` browsing UI on the Tasks page, which is a distinct
 * concern from a per-row administrative CRUD editor. When the store
 * grows dedicated admin actions in a later feat this page can switch
 * over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``epic_id`` and/or
 *     ``status``, with row-level "View", "Edit" and "Delete" actions.
 *     Results are ordered by ``number ASC`` (feat 1, feat 2, …) —
 *     matching the service-layer ordering owned by
 *     ``backend/services/feat.py`` and the ``EpicList`` collapsible UI
 *     convention.
 *   - ``detail`` — read-only view of a single feat: primary key,
 *     ``epic_id``, hierarchical ``number`` (the stable ``F<n>`` label
 *     within the parent epic per DESIGN.md §1.9), ``title``,
 *     ``description``, ``status``, ``estimated_minutes`` /
 *     ``actual_minutes``, the server-managed ``task_count`` and
 *     ``auto_fix_count`` counters, and audit timestamps.
 *   - ``create`` — form that ``POST``s a new feat. ``epic_id`` and
 *     ``title`` are required; ``description`` defaults to ``""``;
 *     ``status`` defaults to ``todo`` (DB ``server_default``);
 *     ``estimated_minutes`` is optional (nullable). ``number`` is
 *     auto-assigned by the service layer as ``MAX(number) + 1`` per
 *     epic — never sent by the client. Concurrent-create races on the
 *     same epic surface as HTTP 409 (the DB-level
 *     ``UNIQUE(epic_id, number)`` constraint,
 *     ``uq_feats_epic_id_number``) and are shown verbatim in the
 *     inline error banner. ``task_count`` and ``auto_fix_count`` are
 *     seeded to ``0`` by the DB ``server_default`` and never accepted
 *     on input.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``title``, ``description``, ``status``, ``estimated_minutes``,
 *     ``actual_minutes``). ``epic_id`` and ``number`` are rendered
 *     read-only — a feat belongs to exactly one epic for its lifetime
 *     and its hierarchical position within the epic must not be
 *     rewritten after the fact (:class:`FeatUpdate` deliberately omits
 *     both, see ``backend/schemas/feat.py``). ``actual_minutes`` is
 *     typically populated automatically from delegation duration but is
 *     exposed here for backfill / correction flows — consistent with
 *     the handling of ``resolved_at`` on the Bug admin page.
 *
 * ``DELETE`` is a hard delete. Inbound foreign keys on ``feats`` are
 * all handled at the DB level — ``tasks.feat_id`` (``ON DELETE
 * CASCADE``), ``delegations.feat_id`` (``ON DELETE SET NULL``) and
 * ``auto_fix_attempts.feat_id`` (``ON DELETE CASCADE``). No RESTRICT
 * dependency check is required, but the confirmation dialog warns the
 * user that the cascade can remove every task under this feat and NULL
 * out the ``feat_id`` reference on any delegations tied to it.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / status values / constraint failures to HTTP 422 and
 * they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/feats`` alongside the other Feat 6
 * CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``,
 * ``/admin/architect-messages``, ``/admin/design-documents``,
 * ``/admin/epics``). It is distinct from ``TasksPage`` (the end-user
 * ``EpicList`` / ``FeatCard`` / ``TaskItem`` surface at
 * ``/projects/:slug/tasks``, DESIGN.md §3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  FeatCreate,
  FeatRead,
  FeatStatus,
  FeatUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the Feat router (see backend/main.py). */
const ENDPOINT = "/feats";

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
 * ``textarea`` / ``select`` / ``number`` input ``value`` is always a
 * string. UUID inputs enforce the canonical shape via the ``pattern``
 * attribute and the backend rejects malformed values with HTTP 422.
 * The ``status`` enum is backed by a ``FeatStatus`` cast at submit
 * time. ``estimated_minutes`` / ``actual_minutes`` accept blank ( →
 * ``null``) or a non-negative integer.
 */
interface FeatFormState {
  epic_id: string;
  title: string;
  description: string;
  status: FeatStatus;
  estimated_minutes: string;
  actual_minutes: string;
}

/**
 * Selectable statuses; mirrors the ``FeatStatus`` literal union and
 * the ``ck_feats_status`` DB CHECK constraint.
 */
const STATUS_OPTIONS: readonly FeatStatus[] = [
  "todo",
  "in_progress",
  "done",
  "failed",
] as const;

/** Fresh-form defaults for the create mode — ``status`` mirrors the DB ``server_default``. */
const EMPTY_FORM: FeatFormState = {
  epic_id: "",
  title: "",
  description: "",
  status: "todo",
  estimated_minutes: "",
  actual_minutes: "",
};

/** Tailwind helper for status pills. */
function statusBadgeClass(value: FeatStatus): string {
  switch (value) {
    case "todo":
      return "bg-gray-100 text-gray-800";
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

/**
 * Parse a "minutes" form field (string from the DOM) into the
 * ``number | null`` shape expected by the backend. Blank strings become
 * ``null`` — matching the "unset / not measured" semantics of the
 * nullable columns. Negative / non-integer / non-numeric values return
 * the string back so ``<input type="number" min="0" step="1">`` can
 * surface the constraint error via the browser's constraint-validation
 * API before submit; the backend would otherwise reject them with a
 * generic 422.
 */
function parseMinutes(raw: string): number | null | string {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed < 0) {
    return raw;
  }
  return parsed;
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

function FeatPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<FeatRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [epicFilter, setEpicFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<FeatStatus | "">("");

  const [detail, setDetail] = useState<FeatRead | null>(null);
  const [form, setForm] = useState<FeatFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<FeatRead>>(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          epic_id: epicFilter.trim() || undefined,
          // Backend exposes the parameter as ``status`` (alias of
          // ``status_filter`` — see backend/api/routes/feats.py).
          status: statusFilter || undefined,
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load feats.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, epicFilter, statusFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<FeatRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load feat.";
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
        const row = await api.get<FeatRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          epic_id: row.epic_id,
          title: row.title,
          description: row.description,
          status: row.status,
          estimated_minutes:
            row.estimated_minutes === null ? "" : String(row.estimated_minutes),
          actual_minutes:
            row.actual_minutes === null ? "" : String(row.actual_minutes),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load feat.";
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
        "Delete this feat? This is a hard delete. Inbound FKs — tasks.feat_id (ON DELETE CASCADE), auto_fix_attempts.feat_id (ON DELETE CASCADE) and delegations.feat_id (ON DELETE SET NULL) — are handled at the DB level: every task under this feat and every auto-fix attempt tied to it will be removed, and any delegations referencing this feat will have their feat_id set to NULL. This may discard a substantial subtree of work; proceed only if you're sure.",
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
        exc instanceof ApiError ? exc.message : "Failed to delete feat.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const estimated = parseMinutes(form.estimated_minutes);
      if (typeof estimated === "string") {
        throw new Error(
          "Estimated minutes must be a non-negative whole number (or left blank).",
        );
      }
      // ``actual_minutes`` is server-managed (measured from delegation
      // duration) and is not accepted on the create path. Matches
      // FeatCreate in backend/schemas/feat.py.
      const payload: FeatCreate = {
        epic_id: form.epic_id.trim(),
        title: form.title.trim(),
        description: form.description,
        status: form.status,
        estimated_minutes: estimated,
      };
      await api.post<FeatRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to create feat.";
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
      // ``epic_id`` and ``number`` are immutable (see
      // backend/schemas/feat.py — FeatUpdate deliberately omits both).
      // ``task_count`` / ``auto_fix_count`` are server-managed counters
      // and not exposed for direct edits either.
      const estimated = parseMinutes(form.estimated_minutes);
      if (typeof estimated === "string") {
        throw new Error(
          "Estimated minutes must be a non-negative whole number (or left blank).",
        );
      }
      const actual = parseMinutes(form.actual_minutes);
      if (typeof actual === "string") {
        throw new Error(
          "Actual minutes must be a non-negative whole number (or left blank).",
        );
      }
      const payload: FeatUpdate = {
        title: form.title.trim(),
        description: form.description,
        status: form.status,
        estimated_minutes: estimated,
        actual_minutes: actual,
      };
      await api.patch<FeatRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to update feat.";
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
          <h2 className="text-xl font-semibold text-gray-900">Feats</h2>
          <p className="text-sm text-gray-600">
            Middle level of the Epic → Feat → Task task hierarchy
            (DESIGN.md §1.9 / §2 ``feats`` table). Ordered by ``number``
            ASC (feat 1, feat 2, …) — auto-assigned per epic at create
            time. Delete is a hard delete and cascades to every task
            (and auto-fix attempt) underneath via the DB-level ``ON
            DELETE CASCADE`` foreign keys; delegations tied to this feat
            have their ``feat_id`` set to NULL.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new feat"
          >
            New Feat
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
        <FeatList
          items={items}
          total={total}
          isLoading={isLoading}
          epicFilter={epicFilter}
          onEpicFilterChange={(value) => {
            setSkip(0);
            setEpicFilter(value);
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
        <FeatDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <FeatForm
          form={form}
          mode={mode.kind}
          isSaving={isSaving}
          isLoading={isLoading && mode.kind === "edit"}
          editingNumber={
            mode.kind === "edit"
              ? (items.find((item) => item.id === mode.id)?.number ?? null)
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

interface FeatListProps {
  items: FeatRead[];
  total: number;
  isLoading: boolean;
  epicFilter: string;
  onEpicFilterChange: (value: string) => void;
  statusFilter: FeatStatus | "";
  onStatusFilterChange: (value: FeatStatus | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function FeatList({
  items,
  total,
  isLoading,
  epicFilter,
  onEpicFilterChange,
  statusFilter,
  onStatusFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: FeatListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label
            htmlFor="epic-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Epic ID
          </label>
          <input
            id="epic-filter"
            type="text"
            value={epicFilter}
            onChange={(event) => onEpicFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show feats under a specific epic. Blank = all epics."
            placeholder="UUID — blank = all epics"
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
              onStatusFilterChange(event.target.value as FeatStatus | "")
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

        <span className="ml-auto text-xs text-gray-500">
          {total} feat{total === 1 ? "" : "s"} total
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
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
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Epic
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Number of tasks under this feat (server-managed counter)."
              >
                Tasks
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Number of Guardian auto-fix attempts linked to this feat (server-managed counter)."
              >
                Auto-fixes
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Architect's estimated duration (minutes) vs measured actual duration. Blank = unset."
              >
                Est / Actual
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Feat ID
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
                  colSpan={10}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading feats…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={10}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No feats match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    F{item.number}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-900">
                    {item.title}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.epic_id}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    {item.task_count}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    {item.auto_fix_count}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    {item.estimated_minutes ?? "—"} /{" "}
                    {item.actual_minutes ?? "—"}
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

interface FeatDetailProps {
  row: FeatRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function FeatDetail({ row, isLoading, onBack, onEdit }: FeatDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading feat…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Feat not found.</p>
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
            Feat ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Number
          </dt>
          <dd className="font-mono text-sm text-gray-900">F{row.number}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Title
          </dt>
          <dd className="text-sm text-gray-900">{row.title}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Description
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900">
            {row.description.length > 0 ? row.description : "—"}
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
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Epic ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.epic_id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Estimated minutes
          </dt>
          <dd className="text-sm text-gray-900">
            {row.estimated_minutes ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Actual minutes
          </dt>
          <dd className="text-sm text-gray-900">
            {row.actual_minutes ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Task count
          </dt>
          <dd className="font-mono text-sm text-gray-900">{row.task_count}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Auto-fix count
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {row.auto_fix_count}
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

interface FeatFormProps {
  form: FeatFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  /** Stable hierarchical number of the row being edited, when known. */
  editingNumber: number | null;
  onChange: (form: FeatFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function FeatForm({
  form,
  mode,
  isSaving,
  isLoading,
  editingNumber,
  onChange,
  onCancel,
  onSubmit,
}: FeatFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<FeatFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading feat…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit feat" : "Create feat"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="epic_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Epic ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → epics, ON DELETE CASCADE; immutable after create)
            </span>
          </label>
          <input
            id="epic_id"
            type="text"
            value={form.epic_id}
            onChange={(event) => patch({ epic_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the epic UUID this feat belongs to."
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
            <span className="ml-1 text-xs font-normal text-gray-500">
              (1–500 chars, required)
            </span>
          </label>
          <input
            id="title"
            type="text"
            value={form.title}
            onChange={(event) => patch({ title: event.target.value })}
            required
            minLength={1}
            maxLength={500}
            placeholder="e.g. JWT login flow"
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
              (optional; defaults to an empty string)
            </span>
          </label>
          <textarea
            id="description"
            value={form.description}
            onChange={(event) => patch({ description: event.target.value })}
            rows={4}
            placeholder="Implementation notes, acceptance criteria, links…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="status"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Status
            <span className="ml-1 text-xs font-normal text-gray-500">
              (todo | in_progress | done | failed; defaults to todo)
            </span>
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({ status: event.target.value as FeatStatus })
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

        {isEdit && editingNumber !== null && (
          <div>
            <label
              htmlFor="number"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Number
              <span className="ml-1 text-xs font-normal text-gray-500">
                (auto-assigned per epic; immutable)
              </span>
            </label>
            <input
              id="number"
              type="text"
              value={`F${editingNumber}`}
              readOnly
              className="block w-full rounded-md border border-gray-300 bg-gray-100 px-3 py-2 font-mono text-sm text-gray-500 shadow-sm"
            />
          </div>
        )}

        <div>
          <label
            htmlFor="estimated_minutes"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Estimated minutes
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional non-negative integer; blank = unset)
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
            placeholder="e.g. 45"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        {isEdit && (
          <div>
            <label
              htmlFor="actual_minutes"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Actual minutes
              <span className="ml-1 text-xs font-normal text-gray-500">
                (typically auto-populated from delegation duration; exposed
                here for backfill / correction)
              </span>
            </label>
            <input
              id="actual_minutes"
              type="number"
              min={0}
              step={1}
              value={form.actual_minutes}
              onChange={(event) =>
                patch({ actual_minutes: event.target.value })
              }
              placeholder="blank = unset"
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
            />
          </div>
        )}
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

export default FeatPage;
