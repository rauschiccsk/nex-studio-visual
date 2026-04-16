/**
 * DesignDocument admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 DesignDocument CRUD surface against the backend REST
 * router mounted at ``/api/v1/design-documents`` (see
 * ``backend/api/routes/design_documents.py``). A ``design_documents``
 * row is one version of a DESIGN.md or BEHAVIOR.md document — either
 * project-level (``module_id IS NULL``, the Foundation document) or
 * per-module. See DESIGN.md §1.9 DesignDocument, §2
 * ``design_documents`` table, §1.5 Architect context injection and D-04
 * Per-module DESIGN.md.
 *
 * Like the other Feat 6 admin pages (``ArchitectSessionPage``,
 * ``ArchitectMessagePage``, ``ProjectModulePage``, …) this surface is
 * deliberately self-contained rather than reaching for a global Zustand
 * store: per DESIGN.md § 3.3 ``specStore`` backs the end-user
 * ``SpecificationPage`` / ``DesignDocViewer`` UI, which is a distinct
 * concern from a per-row administrative CRUD editor. When the store
 * grows dedicated admin actions in a later feat this page can switch
 * over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``module_id``, ``doc_type`` and/or ``approved_by``, with
 *     row-level "View", "Edit" and "Delete" actions. Note that passing
 *     a ``module_id`` filter excludes project-level (Foundation)
 *     documents — this mirrors the backend's indexed-column filter
 *     semantics (DESIGN.md §1.5: "Foundation DESIGN.md == ``module_id
 *     IS NULL AND doc_type='design'``").
 *   - ``detail`` — read-only view of a single document: primary key,
 *     ``project_id``, ``module_id``, ``doc_type``, ``version``,
 *     ``approved_by``, ``approved_at``, full markdown ``content`` and
 *     audit timestamps.
 *   - ``create`` — form that ``POST``s a new document. ``project_id``,
 *     ``doc_type`` and ``content`` are required; ``module_id`` is
 *     optional (blank → Foundation / project-level document);
 *     ``version`` defaults to ``1`` (Pydantic / DB ``server_default``);
 *     ``approved_by`` / ``approved_at`` are normally left blank on
 *     create — a document is approved via a subsequent ``PATCH`` by a
 *     user with the ``ri`` role.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``module_id``, ``content``, ``version``, ``approved_by``,
 *     ``approved_at``). ``project_id`` and ``doc_type`` are rendered
 *     read-only — a document belongs to exactly one project for its
 *     lifetime and ``doc_type`` is an identity discriminator rather
 *     than a mutable property (:class:`DesignDocumentUpdate`
 *     deliberately omits them, see
 *     ``backend/schemas/design_document.py``). When ``approved_by``
 *     transitions from unset to a user UUID and the caller leaves
 *     ``approved_at`` blank, the backend stamps ``approved_at = now()``
 *     automatically — mirroring the ``resolved_at`` auto-stamp on bugs
 *     and the ``closed_at`` auto-stamp on Architect sessions.
 *
 * ``DELETE`` is a hard delete. ``design_documents`` has **no inbound
 * foreign keys** — no other table references it — so no dependency
 * check is required. In normal operation documents are retained as
 * version history (DESIGN.md §3.1 ``DesignDocViewer``); delete is
 * reserved for test fixtures / admin redaction tooling. The outbound
 * FKs ``project_id`` (``ON DELETE CASCADE``), ``module_id`` (``ON
 * DELETE SET NULL``) and ``approved_by`` (``ON DELETE RESTRICT``) keep
 * the row self-consistent when the parent rows change. The
 * confirmation dialog warns the user.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / doc-type values / constraint failures to HTTP 422 and
 * they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/design-documents`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``,
 * ``/admin/architect-messages``). It is distinct from
 * ``SpecificationPage`` (the end-user ``DesignDocViewer`` surface at
 * ``/projects/:slug/spec``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  DesignDocumentCreate,
  DesignDocumentRead,
  DesignDocumentType,
  DesignDocumentUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the DesignDocument router (see backend/main.py). */
const ENDPOINT = "/design-documents";

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
 * ``select`` / ``datetime-local`` / ``number`` input ``value`` is
 * always a string. UUID inputs enforce the canonical shape via the
 * ``pattern`` attribute and the backend rejects malformed values with
 * HTTP 422. The ``doc_type`` enum is backed by a
 * ``DesignDocumentType`` cast at submit time.
 */
interface DesignDocumentFormState {
  project_id: string;
  module_id: string;
  doc_type: DesignDocumentType;
  content: string;
  version: string;
  approved_by: string;
  approved_at: string;
}

/**
 * Selectable document types; mirrors the ``DesignDocumentType`` literal
 * union and the ``ck_design_documents_doc_type`` DB CHECK constraint.
 */
const DOC_TYPE_OPTIONS: readonly DesignDocumentType[] = [
  "design",
  "behavior",
] as const;

/** Fresh-form defaults for the create mode — ``version`` mirrors the DB ``server_default``. */
const EMPTY_FORM: DesignDocumentFormState = {
  project_id: "",
  module_id: "",
  doc_type: "design",
  content: "",
  version: "1",
  approved_by: "",
  approved_at: "",
};

/** Tailwind helper for doc-type pills. */
function docTypeBadgeClass(value: DesignDocumentType): string {
  switch (value) {
    case "design":
      return "bg-sky-100 text-sky-800";
    case "behavior":
      return "bg-amber-100 text-amber-800";
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
 * Convert a ``datetime-local`` input value (``YYYY-MM-DDTHH:mm``) into
 * the ISO-8601 timestamp expected by the backend. ``datetime-local``
 * strings are interpreted as local time by ``new Date(...)`` which is
 * the desired behaviour — the user types a wall-clock time and the
 * resulting ``Date.toISOString()`` normalises it to UTC for
 * transmission. Returns ``null`` for blank input or for values that
 * cannot be parsed (the latter shouldn't happen because the input
 * itself enforces the format).
 */
function parseOptionalDateTime(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const parsed = new Date(trimmed);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/**
 * Convert an ISO-8601 timestamp from the backend into the
 * ``YYYY-MM-DDTHH:mm`` shape required by the ``datetime-local`` input.
 * Returns an empty string when ``iso`` is ``null`` or unparseable so
 * the input renders blank instead of throwing. Local components are
 * rebuilt by hand to avoid the UTC off-by-one issue you hit if you
 * slice ``parsed.toISOString()``.
 */
function isoToDateTimeLocal(iso: string | null): string {
  if (!iso) {
    return "";
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(
    parsed.getDate(),
  )}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

/**
 * Parse an ``input type="number"`` string into a positive integer.
 *
 * Returns ``1`` for blank input so the column falls back to the DB
 * ``server_default`` equivalent. Non-integer or non-positive inputs
 * also fall through to ``1`` — the backend ``Field(ge=1)`` would
 * reject them with HTTP 422 but we prefer a clean client-side
 * round-trip. The HTML ``min`` / ``step`` attributes catch the common
 * typos before submit.
 */
function parsePositiveIntOrDefault(value: string, fallback: number): number {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return fallback;
  }
  const parsed = Number.parseInt(trimmed, 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return fallback;
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

/** Truncate long markdown content for the list preview column. */
function previewContent(value: string, max = 120): string {
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, max).trimEnd()}…`;
}

function DesignDocumentPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<DesignDocumentRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [docTypeFilter, setDocTypeFilter] = useState<DesignDocumentType | "">(
    "",
  );
  const [approvedByFilter, setApprovedByFilter] = useState("");

  const [detail, setDetail] = useState<DesignDocumentRead | null>(null);
  const [form, setForm] = useState<DesignDocumentFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<DesignDocumentRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            module_id: moduleFilter.trim() || undefined,
            doc_type: docTypeFilter || undefined,
            approved_by: approvedByFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load design documents.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter, moduleFilter, docTypeFilter, approvedByFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<DesignDocumentRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load design document.";
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
        const row = await api.get<DesignDocumentRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id,
          module_id: row.module_id ?? "",
          doc_type: row.doc_type,
          content: row.content,
          version: String(row.version),
          approved_by: row.approved_by ?? "",
          approved_at: isoToDateTimeLocal(row.approved_at),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load design document.";
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
        "Delete this design document? This is a hard delete — no other tables reference design_documents, so no dependency check applies. Documents are normally retained as version history (DesignDocViewer); delete is reserved for redaction / test-fixture cleanup.",
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
          : "Failed to delete design document.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const moduleId = form.module_id.trim();
      const approvedBy = form.approved_by.trim();
      const payload: DesignDocumentCreate = {
        project_id: form.project_id.trim(),
        module_id: moduleId ? moduleId : null,
        doc_type: form.doc_type,
        content: form.content,
        version: parsePositiveIntOrDefault(form.version, 1),
        approved_by: approvedBy ? approvedBy : null,
        approved_at: parseOptionalDateTime(form.approved_at),
      };
      await api.post<DesignDocumentRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create design document.";
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
      // ``project_id`` and ``doc_type`` are immutable (see
      // backend/schemas/design_document.py — DesignDocumentUpdate
      // deliberately omits them). We only send the mutable fields.
      //
      // PATCH semantics on the backend: fields that are ``None`` are
      // treated as "leave unchanged" (``module_id``, ``approved_by``
      // and ``approved_at`` are therefore sticky once set). We send
      // ``module_id`` unconditionally since the service applies the
      // update only when the value is non-``None``; blank input is
      // normalised to ``null`` here so the payload shape stays
      // consistent with the create path. Explicit "downgrade to
      // Foundation-level" / "un-approve" transitions are admin-only
      // corrections and not expressible through this UI — matching the
      // backend service contract.
      const moduleId = form.module_id.trim();
      const approvedBy = form.approved_by.trim();
      const payload: DesignDocumentUpdate = {
        module_id: moduleId ? moduleId : null,
        content: form.content,
        version: parsePositiveIntOrDefault(form.version, 1),
        approved_by: approvedBy ? approvedBy : null,
        approved_at: parseOptionalDateTime(form.approved_at),
      };
      await api.patch<DesignDocumentRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update design document.";
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
            Design documents
          </h2>
          <p className="text-sm text-gray-600">
            DESIGN.md / BEHAVIOR.md documents — project-level (Foundation,
            ``module_id == null``) or per-module (DESIGN.md §1.9 / §1.5 /
            D-04). Multiple rows sharing ``(project, module, doc_type)``
            represent version history; newest is shown first. Delete is a
            hard delete reserved for redaction / test fixtures — documents
            are normally retained as version history.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new design document"
          >
            New Document
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
        <DesignDocumentList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
          }}
          moduleFilter={moduleFilter}
          onModuleFilterChange={(value) => {
            setSkip(0);
            setModuleFilter(value);
          }}
          docTypeFilter={docTypeFilter}
          onDocTypeFilterChange={(value) => {
            setSkip(0);
            setDocTypeFilter(value);
          }}
          approvedByFilter={approvedByFilter}
          onApprovedByFilterChange={(value) => {
            setSkip(0);
            setApprovedByFilter(value);
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
        <DesignDocumentDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <DesignDocumentForm
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

interface DesignDocumentListProps {
  items: DesignDocumentRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  moduleFilter: string;
  onModuleFilterChange: (value: string) => void;
  docTypeFilter: DesignDocumentType | "";
  onDocTypeFilterChange: (value: DesignDocumentType | "") => void;
  approvedByFilter: string;
  onApprovedByFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function DesignDocumentList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  moduleFilter,
  onModuleFilterChange,
  docTypeFilter,
  onDocTypeFilterChange,
  approvedByFilter,
  onApprovedByFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: DesignDocumentListProps) {
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
            title="Enter a canonical UUID, or leave blank to show documents across all projects."
            placeholder="UUID — blank = all projects"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="module-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Module ID
          </label>
          <input
            id="module-filter"
            type="text"
            value={moduleFilter}
            onChange={(event) => onModuleFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to show module-level documents for a specific module. Blank = include both module-level and Foundation (project-level) documents."
            placeholder="UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="doc-type-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Type
          </label>
          <select
            id="doc-type-filter"
            value={docTypeFilter}
            onChange={(event) =>
              onDocTypeFilterChange(
                event.target.value as DesignDocumentType | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {DOC_TYPE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="approved-by-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Approved by
          </label>
          <input
            id="approved-by-filter"
            type="text"
            value={approvedByFilter}
            onChange={(event) => onApprovedByFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical user UUID to filter by approver (typically ri-role), or leave blank to show documents from all approvers (including unapproved)."
            placeholder="User UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} document{total === 1 ? "" : "s"} total
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
                Document
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
                Scope
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Type
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Version
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Content
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Approved
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
                  Loading design documents…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No design documents match the current filter.
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
                  <td className="px-4 py-2 text-sm text-gray-700">
                    {item.module_id ? (
                      <span className="font-mono text-[11px] text-gray-700">
                        {item.module_id}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800">
                        Foundation
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${docTypeBadgeClass(item.doc_type)}`}
                    >
                      {item.doc_type}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-700">
                    v{item.version}
                  </td>
                  <td className="max-w-sm truncate px-4 py-2 text-sm text-gray-700">
                    <span title={item.content}>
                      {previewContent(item.content)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {item.approved_by ? (
                      <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 font-medium text-emerald-800">
                        {formatTimestamp(item.approved_at)}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 font-medium text-gray-700">
                        pending
                      </span>
                    )}
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

interface DesignDocumentDetailProps {
  row: DesignDocumentRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function DesignDocumentDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: DesignDocumentDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading design document…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Design document not found.</p>
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
            Document ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Type
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${docTypeBadgeClass(row.doc_type)}`}
            >
              {row.doc_type}
            </span>
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.project_id}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Module ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.module_id ?? "— (Foundation / project-level document)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Version
          </dt>
          <dd className="font-mono text-sm text-gray-900">v{row.version}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Approved by
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.approved_by ?? "— (pending approval)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Approved at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(row.approved_at)}
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
            Content
          </dt>
          <dd className="whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-900">
            {row.content}
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

interface DesignDocumentFormProps {
  form: DesignDocumentFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: DesignDocumentFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function DesignDocumentForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: DesignDocumentFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<DesignDocumentFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading design document…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit design document" : "Create design document"}
      </h3>

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
            title="Enter the project UUID this document belongs to."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="module_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Module ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → project_modules, ON DELETE SET NULL;
              blank = Foundation / project-level document)
            </span>
          </label>
          <input
            id="module_id"
            type="text"
            value={form.module_id}
            onChange={(event) => patch({ module_id: event.target.value })}
            pattern={UUID_PATTERN}
            title="Enter the project module UUID, or leave blank for a Foundation / project-level document."
            placeholder="blank = Foundation / project-level document"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="doc_type"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Type
            <span className="ml-1 text-xs font-normal text-gray-500">
              (immutable after create)
            </span>
          </label>
          <select
            id="doc_type"
            value={form.doc_type}
            onChange={(event) =>
              patch({
                doc_type: event.target.value as DesignDocumentType,
              })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          >
            {DOC_TYPE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="version"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Version
            <span className="ml-1 text-xs font-normal text-gray-500">
              (monotonic integer ≥ 1; defaults to 1; increment on
              regeneration)
            </span>
          </label>
          <input
            id="version"
            type="number"
            min={1}
            step={1}
            value={form.version}
            onChange={(event) => patch({ version: event.target.value })}
            required
            placeholder="1"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="content"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Content
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required markdown body)
            </span>
          </label>
          <textarea
            id="content"
            value={form.content}
            onChange={(event) => patch({ content: event.target.value })}
            required
            rows={14}
            minLength={1}
            placeholder="# Design…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="approved_by"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Approved by
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional user UUID, typically ri-role; FK → users, ON
              DELETE RESTRICT)
            </span>
          </label>
          <input
            id="approved_by"
            type="text"
            value={form.approved_by}
            onChange={(event) => patch({ approved_by: event.target.value })}
            pattern={UUID_PATTERN}
            title="Enter the UUID of the approver (typically a ri-role user), or leave blank for a pending-approval document."
            placeholder="blank = pending approval"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="approved_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Approved at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; stamped automatically when approved_by
              transitions from blank to a user)
            </span>
          </label>
          <input
            id="approved_at"
            type="datetime-local"
            value={form.approved_at}
            onChange={(event) => patch({ approved_at: event.target.value })}
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

export default DesignDocumentPage;
