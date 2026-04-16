/**
 * RawSpecification admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 RawSpecification CRUD surface against the backend
 * REST router mounted at ``/api/v1/raw-specifications`` (see
 * ``backend/api/routes/raw_specifications.py`` and its prefix in
 * ``backend/main.py``). A ``raw_specifications`` row is a verbatim
 * customer specification — plain text / PDF / DOCX — uploaded at the
 * start of the Specification Pipeline (DESIGN.md §1.7
 * RawSpecification, §2 ``raw_specifications`` table, §3.1
 * ``SpecificationPage`` / ``RawSpecInput``). The AI-driven
 * professional-specification generator consumes these rows via the
 * ``professional_specifications.raw_spec_id`` foreign key.
 *
 * Like the other Feat 6 admin pages (``DesignDocumentPage``,
 * ``ProjectModulePage``, ``ModuleDependencyPage``, …) this surface is
 * deliberately self-contained rather than reaching for a global
 * Zustand store: per DESIGN.md § 3.3 ``specStore`` backs the end-user
 * ``SpecificationPage`` / ``RawSpecInput`` UI, which is a distinct
 * concern from a per-row administrative CRUD editor. When the store
 * grows dedicated admin actions in a later feat this page can switch
 * over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``status``, ``created_by``, ``input_format`` and/or
 *     ``language``, with row-level "View", "Edit" and "Delete"
 *     actions. Results are ordered by ``created_at DESC`` (newest
 *     upload first) on the backend — matching the
 *     ``SpecificationPage`` / ``RawSpecInput`` "latest uploads on top"
 *     UI convention (DESIGN.md §3.1).
 *   - ``detail`` — read-only view of a single raw specification:
 *     primary key, ``project_id``, ``created_by``, ``input_format``,
 *     ``language``, ``status``, full ``input_text`` body and audit
 *     timestamps.
 *   - ``create`` — form that ``POST``s a new raw specification.
 *     ``project_id``, ``input_text`` and ``created_by`` are required;
 *     ``input_format`` defaults to ``text``, ``language`` defaults to
 *     ``sk`` and ``status`` defaults to ``pending`` (all mirror the DB
 *     ``server_default``).
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``input_text``, ``input_format``, ``language``, ``status``).
 *     ``project_id`` and ``created_by`` are rendered read-only — a
 *     specification belongs to exactly one project and uploader for
 *     its lifetime (resubmissions are new rows, not a reassignment).
 *     :class:`RawSpecificationUpdate` deliberately omits both columns
 *     (see ``backend/schemas/raw_specification.py``).
 *
 * ``DELETE`` is a hard delete. The single inbound FK
 * (``professional_specifications.raw_spec_id``) uses
 * ``ON DELETE CASCADE`` so dependent AI-generated professional
 * specifications are removed automatically at the DB level — no
 * RESTRICT dependency check is required in ``DELETE``. In normal
 * operation raw specifications are retained as submission history
 * (DESIGN.md §3.1 ``SpecificationPage``); delete is reserved for test
 * fixtures / admin redaction tooling where the upload itself must go.
 * The confirmation dialog warns the user.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / enum values / constraint failures to HTTP 422 and
 * missing rows to HTTP 404; all are shown verbatim in the inline
 * error banner.
 *
 * This page sits under ``/admin/raw-specifications`` alongside the
 * other Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/design-documents``, ``/admin/kb-documents``,
 * ``/admin/module-dependencies``, …). It is distinct from
 * ``SpecificationPage`` (the end-user ``RawSpecInput`` surface at
 * ``/projects/:slug/spec``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  RawSpecificationCreate,
  RawSpecificationInputFormat,
  RawSpecificationRead,
  RawSpecificationStatus,
  RawSpecificationUpdate,
} from "../types";

/** REST prefix for the RawSpecification router (see backend/main.py). */
const ENDPOINT = "/raw-specifications";

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
 * ``select`` / ``textarea`` input ``value`` is always a string. UUID
 * inputs enforce the canonical shape via the ``pattern`` attribute
 * and the backend rejects malformed values with HTTP 422. The
 * ``input_format`` and ``status`` enums are backed by their literal
 * unions at submit time.
 */
interface RawSpecificationFormState {
  project_id: string;
  input_text: string;
  input_format: RawSpecificationInputFormat;
  language: string;
  status: RawSpecificationStatus;
  created_by: string;
}

/**
 * Selectable input formats; mirrors the ``RawSpecificationInputFormat``
 * literal union and the ``ck_raw_specifications_input_format`` DB
 * CHECK constraint (``text | pdf | docx``).
 */
const INPUT_FORMAT_OPTIONS: readonly RawSpecificationInputFormat[] = [
  "text",
  "pdf",
  "docx",
] as const;

/**
 * Selectable processing statuses; mirrors the ``RawSpecificationStatus``
 * literal union and the ``ck_raw_specifications_status`` DB CHECK
 * constraint (``pending | processing | done | failed``).
 */
const STATUS_OPTIONS: readonly RawSpecificationStatus[] = [
  "pending",
  "processing",
  "done",
  "failed",
] as const;

/**
 * Fresh-form defaults for the create mode — mirror the DB
 * ``server_default`` values (``text``, ``sk`` and ``pending``
 * respectively) so callers that just fill in the required fields get
 * the same row the backend would synthesise on its own.
 */
const EMPTY_FORM: RawSpecificationFormState = {
  project_id: "",
  input_text: "",
  input_format: "text",
  language: "sk",
  status: "pending",
  created_by: "",
};

/** Tailwind helper for input-format pills. */
function inputFormatBadgeClass(value: RawSpecificationInputFormat): string {
  switch (value) {
    case "text":
      return "bg-slate-100 text-slate-800";
    case "pdf":
      return "bg-rose-100 text-rose-800";
    case "docx":
      return "bg-sky-100 text-sky-800";
  }
}

/** Tailwind helper for status pills. */
function statusBadgeClass(value: RawSpecificationStatus): string {
  switch (value) {
    case "pending":
      return "bg-gray-200 text-gray-700";
    case "processing":
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
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend). Rendered on UUID inputs so
 * obvious typos are caught by the browser's constraint-validation API
 * before the form is submitted — the backend would otherwise reject
 * them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** Truncate long raw-text content for the list preview column. */
function previewContent(value: string, max = 120): string {
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, max).trimEnd()}…`;
}

function RawSpecificationPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<RawSpecificationRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<
    RawSpecificationStatus | ""
  >("");
  const [createdByFilter, setCreatedByFilter] = useState("");
  const [inputFormatFilter, setInputFormatFilter] = useState<
    RawSpecificationInputFormat | ""
  >("");
  const [languageFilter, setLanguageFilter] = useState("");

  const [detail, setDetail] = useState<RawSpecificationRead | null>(null);
  const [form, setForm] = useState<RawSpecificationFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<RawSpecificationRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            status: statusFilter || undefined,
            created_by: createdByFilter.trim() || undefined,
            input_format: inputFormatFilter || undefined,
            language: languageFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load raw specifications.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [
    skip,
    projectFilter,
    statusFilter,
    createdByFilter,
    inputFormatFilter,
    languageFilter,
  ]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<RawSpecificationRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load raw specification.";
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
        const row = await api.get<RawSpecificationRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setDetail(row);
        setForm({
          project_id: row.project_id,
          input_text: row.input_text,
          input_format: row.input_format,
          language: row.language,
          status: row.status,
          created_by: row.created_by,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load raw specification.";
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
        "Delete this raw specification? This is a hard delete — the inbound FK from professional_specifications uses ON DELETE CASCADE, so any AI-generated professional specification(s) derived from this upload will be removed automatically. Raw specifications are normally retained as submission history; delete is reserved for redaction / test-fixture cleanup.",
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
          : "Failed to delete raw specification.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: RawSpecificationCreate = {
        project_id: form.project_id.trim(),
        input_text: form.input_text,
        input_format: form.input_format,
        language: form.language.trim() || "sk",
        status: form.status,
        created_by: form.created_by.trim(),
      };
      await api.post<RawSpecificationRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create raw specification.";
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
      // ``project_id`` and ``created_by`` are immutable (see
      // backend/schemas/raw_specification.py — RawSpecificationUpdate
      // deliberately omits them). We only send the mutable fields.
      //
      // PATCH semantics on the backend: fields that are ``None`` are
      // treated as "leave unchanged". We send every mutable field
      // unconditionally since the form always has them populated from
      // the seeded row — omitting would require an extra "has changed"
      // diff that buys nothing here.
      const payload: RawSpecificationUpdate = {
        input_text: form.input_text,
        input_format: form.input_format,
        language: form.language.trim() || "sk",
        status: form.status,
      };
      await api.patch<RawSpecificationRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update raw specification.";
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
            Raw specifications
          </h2>
          <p className="text-sm text-gray-600">
            Verbatim customer specification uploads — plain text / PDF /
            DOCX — feeding the Specification Pipeline (DESIGN.md §1.7,
            §3.1 ``SpecificationPage`` / ``RawSpecInput``). A project
            may hold many rows (historical submissions, re-uploads,
            iterations). Delete is a hard delete reserved for redaction
            / test fixtures — derived professional specifications
            cascade-delete automatically.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new raw specification"
          >
            New Specification
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
        <RawSpecificationList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
          }}
          statusFilter={statusFilter}
          onStatusFilterChange={(value) => {
            setSkip(0);
            setStatusFilter(value);
          }}
          createdByFilter={createdByFilter}
          onCreatedByFilterChange={(value) => {
            setSkip(0);
            setCreatedByFilter(value);
          }}
          inputFormatFilter={inputFormatFilter}
          onInputFormatFilterChange={(value) => {
            setSkip(0);
            setInputFormatFilter(value);
          }}
          languageFilter={languageFilter}
          onLanguageFilterChange={(value) => {
            setSkip(0);
            setLanguageFilter(value);
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
        <RawSpecificationDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <RawSpecificationForm
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

interface RawSpecificationListProps {
  items: RawSpecificationRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  statusFilter: RawSpecificationStatus | "";
  onStatusFilterChange: (value: RawSpecificationStatus | "") => void;
  createdByFilter: string;
  onCreatedByFilterChange: (value: string) => void;
  inputFormatFilter: RawSpecificationInputFormat | "";
  onInputFormatFilterChange: (
    value: RawSpecificationInputFormat | "",
  ) => void;
  languageFilter: string;
  onLanguageFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function RawSpecificationList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  statusFilter,
  onStatusFilterChange,
  createdByFilter,
  onCreatedByFilterChange,
  inputFormatFilter,
  onInputFormatFilterChange,
  languageFilter,
  onLanguageFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: RawSpecificationListProps) {
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
            title="Enter a canonical UUID, or leave blank to show specifications across all projects."
            placeholder="UUID — blank = all projects"
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
              onStatusFilterChange(
                event.target.value as RawSpecificationStatus | "",
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
            htmlFor="created-by-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Created by
          </label>
          <input
            id="created-by-filter"
            type="text"
            value={createdByFilter}
            onChange={(event) => onCreatedByFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical user UUID to restrict to that uploader, or leave blank to show submissions from all users."
            placeholder="User UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="input-format-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Format
          </label>
          <select
            id="input-format-filter"
            value={inputFormatFilter}
            onChange={(event) =>
              onInputFormatFilterChange(
                event.target.value as RawSpecificationInputFormat | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {INPUT_FORMAT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="language-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Language
          </label>
          <input
            id="language-filter"
            type="text"
            value={languageFilter}
            onChange={(event) => onLanguageFilterChange(event.target.value)}
            maxLength={10}
            title="ISO-style language code (e.g. sk, en). Blank = all languages."
            placeholder="sk / en / …"
            className="w-32 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} specification{total === 1 ? "" : "s"} total
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
                Specification
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
                Uploader
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Format
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Lang
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
                Text
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
                  colSpan={9}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading raw specifications…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No raw specifications match the current filter.
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
                    {item.project_id}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-gray-500">
                    {item.created_by}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${inputFormatBadgeClass(item.input_format)}`}
                    >
                      {item.input_format}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-700">
                    {item.language}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="max-w-sm truncate px-4 py-2 text-sm text-gray-700">
                    <span title={item.input_text}>
                      {previewContent(item.input_text)}
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

interface RawSpecificationDetailProps {
  row: RawSpecificationRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function RawSpecificationDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: RawSpecificationDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading raw specification…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Raw specification not found.</p>
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
            Specification ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-400">
              (immutable)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.project_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Uploader
            <span className="ml-1 text-xs font-normal text-gray-400">
              (created_by — immutable)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.created_by}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Format
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${inputFormatBadgeClass(row.input_format)}`}
            >
              {row.input_format}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Language
          </dt>
          <dd className="font-mono text-sm text-gray-900">{row.language}</dd>
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
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Input text
          </dt>
          <dd className="whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-900">
            {row.input_text}
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

interface RawSpecificationFormProps {
  form: RawSpecificationFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: RawSpecificationFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function RawSpecificationForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: RawSpecificationFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<RawSpecificationFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading raw specification…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit raw specification" : "Create raw specification"}
      </h3>
      <p className="text-sm text-gray-600">
        ``project_id`` and ``created_by`` are immutable after create — a
        specification belongs to exactly one project and uploader for
        its lifetime (resubmissions are new rows, not a reassignment).
        ``input_format`` defaults to ``text``, ``language`` to ``sk``
        and ``status`` to ``pending`` (server defaults on create).
      </p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="project_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → projects, ON DELETE CASCADE; immutable after
              create)
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
            title="Enter the project UUID this specification belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="created_by"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Uploader
            <span className="ml-1 text-xs font-normal text-gray-500">
              (created_by UUID; FK → users; immutable after create)
            </span>
          </label>
          <input
            id="created_by"
            type="text"
            value={form.created_by}
            onChange={(event) => patch({ created_by: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the UUID of the user uploading this specification."
            placeholder="e.g. 7fcd8c42-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="input_format"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Format
            <span className="ml-1 text-xs font-normal text-gray-500">
              (text | pdf | docx)
            </span>
          </label>
          <select
            id="input_format"
            value={form.input_format}
            onChange={(event) =>
              patch({
                input_format: event.target
                  .value as RawSpecificationInputFormat,
              })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {INPUT_FORMAT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="language"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Language
            <span className="ml-1 text-xs font-normal text-gray-500">
              (ISO-style code, max 10 chars; defaults to sk)
            </span>
          </label>
          <input
            id="language"
            type="text"
            value={form.language}
            onChange={(event) => patch({ language: event.target.value })}
            required
            minLength={1}
            maxLength={10}
            placeholder="sk"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="status"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Status
            <span className="ml-1 text-xs font-normal text-gray-500">
              (pending | processing | done | failed)
            </span>
          </label>
          <select
            id="status"
            value={form.status}
            onChange={(event) =>
              patch({
                status: event.target.value as RawSpecificationStatus,
              })
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
            htmlFor="input_text"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Input text
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required — the verbatim customer specification body)
            </span>
          </label>
          <textarea
            id="input_text"
            value={form.input_text}
            onChange={(event) => patch({ input_text: event.target.value })}
            required
            rows={14}
            minLength={1}
            placeholder="Paste or type the customer's raw specification…"
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

export default RawSpecificationPage;
