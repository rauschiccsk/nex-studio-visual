/**
 * GuardianPrecedent admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 Guardian-precedents CRUD surface against the backend
 * REST router mounted at ``/api/v1/guardian-precedents`` (see
 * ``backend/api/routes/guardian_precedents.py``).  The page is self-
 * contained: it owns its own local state rather than reaching for a
 * Zustand store because the `precedentStore` has not yet been introduced
 * in DESIGN.md § 3.3.  When the global store is added in a later feat
 * this page can switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated grid with verdict filter, plus row-level
 *     "View", "Edit" and "Delete" actions.
 *   - ``detail`` — read-only view of a single precedent.
 *   - ``create`` — form that ``POST``s a new precedent.
 *   - ``edit``   — form that ``PATCH``es the mutable fields (description
 *     and verdict) of an existing precedent.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  GuardianPrecedentCreate,
  GuardianPrecedentRead,
  GuardianPrecedentUpdate,
  GuardianVerdict,
  PaginatedResponse,
} from "../types";

/** REST prefix for the Guardian precedent router (see backend/main.py). */
const ENDPOINT = "/guardian-precedents";

/** Page size used by the list view. Matches the backend default. */
const PAGE_SIZE = 20;

/** Finite mode state keeps the render logic explicit and linter-friendly. */
type Mode =
  | { kind: "list" }
  | { kind: "detail"; id: string }
  | { kind: "create" }
  | { kind: "edit"; id: string };

/** Shape of the mutable fields in the create / edit forms. */
interface PrecedentFormState {
  pattern_hash: string;
  pattern_description: string;
  verdict: GuardianVerdict;
  created_by: string;
}

/** Selectable verdicts; mirrors the ``GuardianVerdict`` literal union. */
const VERDICT_OPTIONS: readonly GuardianVerdict[] = [
  "allow",
  "notice",
  "block",
] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: PrecedentFormState = {
  pattern_hash: "",
  pattern_description: "",
  verdict: "allow",
  created_by: "",
};

/** Tailwind helper for verdict pills. */
function verdictBadgeClass(verdict: GuardianVerdict): string {
  switch (verdict) {
    case "allow":
      return "bg-emerald-100 text-emerald-800";
    case "notice":
      return "bg-amber-100 text-amber-800";
    case "block":
      return "bg-red-100 text-red-800";
  }
}

/** Format an ISO timestamp as a locale date-time string, tolerant of bad input. */
function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}

function GuardianPrecedentPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<GuardianPrecedentRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [verdictFilter, setVerdictFilter] = useState<GuardianVerdict | "">("");

  const [detail, setDetail] = useState<GuardianPrecedentRead | null>(null);
  const [form, setForm] = useState<PrecedentFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<GuardianPrecedentRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            verdict: verdictFilter || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load precedents.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, verdictFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<GuardianPrecedentRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load precedent.";
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
        const row = await api.get<GuardianPrecedentRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          pattern_hash: row.pattern_hash,
          pattern_description: row.pattern_description,
          verdict: row.verdict,
          created_by: row.created_by ?? "",
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load precedent.";
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
    if (!window.confirm("Delete this precedent? This cannot be undone.")) {
      return;
    }
    setError(null);
    try {
      await api.delete(`${ENDPOINT}/${id}`);
      await loadList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to delete precedent.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: GuardianPrecedentCreate = {
        pattern_hash: form.pattern_hash.trim(),
        pattern_description: form.pattern_description.trim(),
        verdict: form.verdict,
        created_by: form.created_by.trim() ? form.created_by.trim() : null,
      };
      await api.post<GuardianPrecedentRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to create precedent.";
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
      const payload: GuardianPrecedentUpdate = {
        pattern_description: form.pattern_description.trim(),
        verdict: form.verdict,
      };
      await api.patch<GuardianPrecedentRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to update precedent.";
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
            Guardian Precedents
          </h2>
          <p className="text-sm text-gray-600">
            Allowlist entries that tell the Guardian pipeline how to treat a
            recurring finding pattern.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new precedent"
          >
            New Precedent
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
        <PrecedentList
          items={items}
          total={total}
          isLoading={isLoading}
          verdictFilter={verdictFilter}
          onVerdictFilterChange={(value) => {
            setSkip(0);
            setVerdictFilter(value);
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
        <PrecedentDetail
          precedent={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <PrecedentForm
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

interface PrecedentListProps {
  items: GuardianPrecedentRead[];
  total: number;
  isLoading: boolean;
  verdictFilter: GuardianVerdict | "";
  onVerdictFilterChange: (value: GuardianVerdict | "") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function PrecedentList({
  items,
  total,
  isLoading,
  verdictFilter,
  onVerdictFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: PrecedentListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label
          htmlFor="verdict-filter"
          className="text-sm font-medium text-gray-700"
        >
          Filter by verdict:
        </label>
        <select
          id="verdict-filter"
          value={verdictFilter}
          onChange={(event) =>
            onVerdictFilterChange(event.target.value as GuardianVerdict | "")
          }
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
        >
          <option value="">All</option>
          {VERDICT_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <span className="ml-auto text-xs text-gray-500">
          {total} precedent{total === 1 ? "" : "s"} total
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
                Pattern hash
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Description
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Verdict
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
                  colSpan={5}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading precedents…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No precedents match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="truncate px-4 py-2 font-mono text-xs text-gray-700">
                    {item.pattern_hash.slice(0, 12)}…
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-900">
                    {item.pattern_description}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${verdictBadgeClass(item.verdict)}`}
                    >
                      {item.verdict}
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

interface PrecedentDetailProps {
  precedent: GuardianPrecedentRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function PrecedentDetail({
  precedent,
  isLoading,
  onBack,
  onEdit,
}: PrecedentDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading precedent…
      </div>
    );
  }
  if (!precedent) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Precedent not found.</p>
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
            {precedent.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Verdict
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${verdictBadgeClass(precedent.verdict)}`}
            >
              {precedent.verdict}
            </span>
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Pattern hash
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {precedent.pattern_hash}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Pattern description
          </dt>
          <dd className="whitespace-pre-wrap text-sm text-gray-900">
            {precedent.pattern_description}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created by
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {precedent.created_by ?? "— (system)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(precedent.created_at)}
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

interface PrecedentFormProps {
  form: PrecedentFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: PrecedentFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function PrecedentForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: PrecedentFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<PrecedentFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading precedent…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit precedent" : "Create precedent"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="pattern_hash"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Pattern hash
            <span className="ml-1 text-xs font-normal text-gray-500">
              (SHA-256 hex, 64 chars — immutable after create)
            </span>
          </label>
          <input
            id="pattern_hash"
            type="text"
            value={form.pattern_hash}
            onChange={(event) => patch({ pattern_hash: event.target.value })}
            required
            readOnly={isEdit}
            minLength={64}
            maxLength={64}
            pattern="[0-9a-fA-F]{64}"
            placeholder="e.g. 5e88489…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="pattern_description"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Pattern description
          </label>
          <textarea
            id="pattern_description"
            value={form.pattern_description}
            onChange={(event) =>
              patch({ pattern_description: event.target.value })
            }
            required
            minLength={1}
            rows={4}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="verdict"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Verdict
          </label>
          <select
            id="verdict"
            value={form.verdict}
            onChange={(event) =>
              patch({ verdict: event.target.value as GuardianVerdict })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {VERDICT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="created_by"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Created by
            <span className="ml-1 text-xs font-normal text-gray-500">
              (user UUID; leave blank for system seed)
            </span>
          </label>
          <input
            id="created_by"
            type="text"
            value={form.created_by}
            onChange={(event) => patch({ created_by: event.target.value })}
            readOnly={isEdit}
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
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

export default GuardianPrecedentPage;
