/**
 * KbDocument admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 KbDocument CRUD surface against the backend REST
 * router mounted at ``/api/v1/kb-documents`` (see
 * ``backend/api/routes/kb_documents.py``). A ``kb_documents`` row is one
 * metadata record catalogueing an on-disk knowledge-base document under
 * ``/home/icc/knowledge`` and pairing it with its Qdrant vector
 * representation — DESIGN.md §1.4 Knowledge Base, §1.10 KbDocument, §2
 * ``kb_documents`` table and §3.1 ``KnowledgeBasePage``.
 *
 * Like the other Feat 6 admin pages (``DesignDocumentPage``,
 * ``ArchitectSessionPage``, ``AutoFixAttemptPage``, …) this surface is
 * deliberately self-contained rather than reaching for a global Zustand
 * store: per DESIGN.md §3.3 the KB content itself is browsed via the
 * end-user ``KnowledgeBasePage`` at ``/projects/:slug/kb`` (a distinct
 * concern from a per-row administrative CRUD editor). When the KB flows
 * grow dedicated admin actions in a later feat this page can switch
 * over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``module_id``, ``doc_category`` and/or ``qdrant_point_id``, with
 *     row-level "View", "Edit" and "Delete" actions. Results are
 *     ordered by ``created_at DESC`` (newest first) — matching the
 *     service-layer ordering owned by
 *     ``backend/services/kb_document.py`` and the "newest first"
 *     convention used by the end-user ``KnowledgeBasePage``. Note that
 *     passing a ``project_id`` filter excludes ICC-wide documents
 *     (``project_id IS NULL``) and passing a ``module_id`` filter
 *     excludes project-level documents (``module_id IS NULL``) — this
 *     mirrors the backend's indexed-column filter semantics.
 *   - ``detail`` — read-only view of a single document: primary key,
 *     ``project_id``, ``module_id``, ``title``, ``file_path``,
 *     ``doc_category``, ``qdrant_collection``, ``qdrant_point_id``,
 *     ``indexed_at`` and audit timestamps.
 *   - ``create`` — form that ``POST``s a new document. ``title``,
 *     ``file_path`` and ``doc_category`` are required. ``project_id``
 *     is optional (blank → ICC-wide document, DESIGN.md §1.4 "NULL =
 *     ICC-wide document"); ``module_id`` is optional (blank →
 *     project-level or ICC-wide when ``project_id`` is also blank,
 *     DESIGN.md §1.4 "NULL = project-level or ICC-wide").
 *     ``qdrant_collection``, ``qdrant_point_id`` and ``indexed_at`` are
 *     optional and normally left blank at create — they are populated
 *     by a subsequent Qdrant indexing run (DESIGN.md §1.4 "Qdrant
 *     reindexing is triggered by Zoltán via UI after file writes (not
 *     automatic)").
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``module_id``, ``title``, ``file_path``, ``qdrant_collection``,
 *     ``qdrant_point_id``, ``indexed_at``). ``project_id`` and
 *     ``doc_category`` are rendered read-only — a document's scope
 *     (project-specific vs ICC-wide) and its identity category are
 *     fixed at creation time (:class:`KbDocumentUpdate` deliberately
 *     omits both, see ``backend/schemas/kb_document.py``). PATCH
 *     semantics: fields that are blank / ``null`` are treated as
 *     "leave unchanged" by the service; the explicit-null "downgrade
 *     to project-level" or "un-index" transitions are not expressible
 *     through this UI — ``module_id`` already clears automatically on
 *     module deletion via ``ON DELETE SET NULL`` and the un-index
 *     corrections are admin-only.
 *
 * ``DELETE`` is a hard delete. ``kb_documents`` has **no inbound
 * foreign keys** — no other table references it — so no RESTRICT
 * dependency check is required. Note that KB deletion is
 * metadata-only: the underlying file on the ANDROS filesystem and the
 * Qdrant point are **not** removed by this endpoint (DESIGN.md §1.4).
 * The confirmation dialog warns the user. Deleting the parent
 * ``projects`` row cascades automatically via ``kb_documents.project_id
 * ON DELETE CASCADE``; deleting the parent ``project_modules`` row
 * downgrades the document to project-level via ``module_id ON DELETE
 * SET NULL``.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid foreign
 * keys / doc-category values / constraint failures to HTTP 422 and they
 * are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/kb-documents`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/bugs``, ``/admin/bug-fix-tasks``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``,
 * ``/admin/migration-id-maps``, ``/admin/project-members``,
 * ``/admin/project-modules``, ``/admin/architect-sessions``,
 * ``/admin/architect-messages``, ``/admin/design-documents``,
 * ``/admin/epics``, ``/admin/feats``, ``/admin/auto-fix-attempts``).
 * It is distinct from ``KnowledgeBasePage`` (the end-user KB-browser
 * surface at ``/projects/:slug/kb``, DESIGN.md §3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  KbDocumentCategory,
  KbDocumentCreate,
  KbDocumentRead,
  KbDocumentUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the KbDocument router (see backend/main.py). */
const ENDPOINT = "/kb-documents";

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
 * ``select`` / ``datetime-local`` input ``value`` is always a string.
 * UUID inputs enforce the canonical shape via the ``pattern`` attribute
 * and the backend rejects malformed values with HTTP 422. The
 * ``doc_category`` enum is backed by a :type:`KbDocumentCategory` cast
 * at submit time.
 */
interface KbDocumentFormState {
  project_id: string;
  module_id: string;
  title: string;
  file_path: string;
  doc_category: KbDocumentCategory;
  qdrant_collection: string;
  qdrant_point_id: string;
  indexed_at: string;
}

/**
 * Selectable document categories; mirrors the ``KbDocumentCategory``
 * literal union and the ``ck_kb_documents_doc_category`` DB CHECK
 * constraint.
 */
const DOC_CATEGORY_OPTIONS: readonly KbDocumentCategory[] = [
  "standards",
  "decisions",
  "lessons",
  "patterns",
  "design",
  "behavior",
  "session",
] as const;

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: KbDocumentFormState = {
  project_id: "",
  module_id: "",
  title: "",
  file_path: "",
  doc_category: "standards",
  qdrant_collection: "",
  qdrant_point_id: "",
  indexed_at: "",
};

/** Tailwind helper for doc-category pills — colours are stable per category. */
function docCategoryBadgeClass(value: KbDocumentCategory): string {
  switch (value) {
    case "standards":
      return "bg-slate-100 text-slate-800";
    case "decisions":
      return "bg-emerald-100 text-emerald-800";
    case "lessons":
      return "bg-amber-100 text-amber-800";
    case "patterns":
      return "bg-sky-100 text-sky-800";
    case "design":
      return "bg-indigo-100 text-indigo-800";
    case "behavior":
      return "bg-violet-100 text-violet-800";
    case "session":
      return "bg-rose-100 text-rose-800";
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

/** Render an optional string value for the detail / list views. */
function formatOptional(value: string | null | undefined): string {
  return value === null || value === undefined || value.length === 0
    ? "—"
    : value;
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
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend). Rendered on UUID inputs so
 * obvious typos are caught by the browser's constraint-validation API
 * before the form is submitted — the backend would otherwise reject
 * them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

function KbDocumentPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<KbDocumentRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [docCategoryFilter, setDocCategoryFilter] = useState<
    KbDocumentCategory | ""
  >("");
  const [qdrantPointIdFilter, setQdrantPointIdFilter] = useState("");

  const [detail, setDetail] = useState<KbDocumentRead | null>(null);
  const [form, setForm] = useState<KbDocumentFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<KbDocumentRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
            module_id: moduleFilter.trim() || undefined,
            doc_category: docCategoryFilter || undefined,
            qdrant_point_id: qdrantPointIdFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load KB documents.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [
    skip,
    projectFilter,
    moduleFilter,
    docCategoryFilter,
    qdrantPointIdFilter,
  ]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<KbDocumentRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load KB document.";
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
        const row = await api.get<KbDocumentRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          project_id: row.project_id ?? "",
          module_id: row.module_id ?? "",
          title: row.title,
          file_path: row.file_path,
          doc_category: row.doc_category,
          qdrant_collection: row.qdrant_collection ?? "",
          qdrant_point_id: row.qdrant_point_id ?? "",
          indexed_at: isoToDateTimeLocal(row.indexed_at),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load KB document.";
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
        "Delete this KB document? This is a hard delete — kb_documents has no inbound foreign keys, so no dependency check applies. Note: KB deletion is metadata-only — the underlying file on the ANDROS filesystem and the Qdrant point are NOT removed here (DESIGN.md §1.4). Callers that need to drop the file / reindex Qdrant must coordinate that in a higher-level workflow.",
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
        exc instanceof ApiError ? exc.message : "Failed to delete KB document.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const projectId = form.project_id.trim();
      const moduleId = form.module_id.trim();
      const qdrantCollection = form.qdrant_collection.trim();
      const qdrantPointId = form.qdrant_point_id.trim();
      const payload: KbDocumentCreate = {
        project_id: projectId.length === 0 ? null : projectId,
        module_id: moduleId.length === 0 ? null : moduleId,
        title: form.title,
        file_path: form.file_path,
        doc_category: form.doc_category,
        qdrant_collection:
          qdrantCollection.length === 0 ? null : qdrantCollection,
        qdrant_point_id: qdrantPointId.length === 0 ? null : qdrantPointId,
        indexed_at: parseOptionalDateTime(form.indexed_at),
      };
      await api.post<KbDocumentRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to create KB document.";
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
      // ``project_id`` and ``doc_category`` are immutable (see
      // backend/schemas/kb_document.py — KbDocumentUpdate deliberately
      // omits them). We only send the mutable fields.
      //
      // PATCH semantics on the backend: fields that are ``None`` are
      // treated as "leave unchanged" (``module_id``,
      // ``qdrant_collection``, ``qdrant_point_id`` and ``indexed_at``
      // are therefore sticky once set). Blank inputs are dropped from
      // the payload rather than sent as ``null``. Explicit "downgrade
      // to project-level" / "un-index" transitions are admin-only
      // corrections and not expressible through this UI — matching the
      // backend service contract. ``module_id -> NULL`` already happens
      // automatically on module deletion via ``ON DELETE SET NULL``.
      const moduleId = form.module_id.trim();
      const qdrantCollection = form.qdrant_collection.trim();
      const qdrantPointId = form.qdrant_point_id.trim();
      const indexedAt = parseOptionalDateTime(form.indexed_at);
      const payload: KbDocumentUpdate = {
        title: form.title,
        file_path: form.file_path,
      };
      if (moduleId.length > 0) {
        payload.module_id = moduleId;
      }
      if (qdrantCollection.length > 0) {
        payload.qdrant_collection = qdrantCollection;
      }
      if (qdrantPointId.length > 0) {
        payload.qdrant_point_id = qdrantPointId;
      }
      if (indexedAt !== null) {
        payload.indexed_at = indexedAt;
      }
      await api.patch<KbDocumentRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to update KB document.";
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
          <h2 className="text-xl font-semibold text-gray-900">KB documents</h2>
          <p className="text-sm text-gray-600">
            Knowledge-base document metadata — one row per on-disk document
            under ``/home/icc/knowledge`` paired with its Qdrant vector
            representation (DESIGN.md §1.4 / §1.10 / §2 ``kb_documents``
            table). ``project_id IS NULL`` denotes an ICC-wide document;
            ``module_id IS NULL`` denotes a project-level (or ICC-wide when
            ``project_id`` is also NULL) document. Ordered by created_at
            DESC (newest first). Delete is metadata-only — the underlying
            file and Qdrant point are not removed.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new KB document"
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
        <KbDocumentList
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
          docCategoryFilter={docCategoryFilter}
          onDocCategoryFilterChange={(value) => {
            setSkip(0);
            setDocCategoryFilter(value);
          }}
          qdrantPointIdFilter={qdrantPointIdFilter}
          onQdrantPointIdFilterChange={(value) => {
            setSkip(0);
            setQdrantPointIdFilter(value);
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
        <KbDocumentDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <KbDocumentForm
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

interface KbDocumentListProps {
  items: KbDocumentRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  moduleFilter: string;
  onModuleFilterChange: (value: string) => void;
  docCategoryFilter: KbDocumentCategory | "";
  onDocCategoryFilterChange: (value: KbDocumentCategory | "") => void;
  qdrantPointIdFilter: string;
  onQdrantPointIdFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function KbDocumentList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  moduleFilter,
  onModuleFilterChange,
  docCategoryFilter,
  onDocCategoryFilterChange,
  qdrantPointIdFilter,
  onQdrantPointIdFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: KbDocumentListProps) {
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
            title="Enter a canonical UUID to show documents belonging to a specific project. Blank = all projects (includes ICC-wide documents). Supplying a value filters OUT ICC-wide (project_id IS NULL) documents."
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
            title="Enter a canonical UUID to show module-level documents for a specific module. Blank = include both module-level and project-level / ICC-wide documents."
            placeholder="UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="doc-category-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Category
          </label>
          <select
            id="doc-category-filter"
            value={docCategoryFilter}
            onChange={(event) =>
              onDocCategoryFilterChange(
                event.target.value as KbDocumentCategory | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {DOC_CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="qdrant-point-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Qdrant point ID
          </label>
          <input
            id="qdrant-point-filter"
            type="text"
            value={qdrantPointIdFilter}
            onChange={(event) =>
              onQdrantPointIdFilterChange(event.target.value)
            }
            title="Reverse-lookup — fetch the metadata row for a specific Qdrant point id. Blank = all points."
            placeholder="Point ID — blank = all"
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
                Title
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Category
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
                Module
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                File path
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Indexed
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
                  Loading KB documents…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No KB documents match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="max-w-xs truncate px-4 py-2 text-sm font-medium text-gray-900">
                    <span title={item.title}>{item.title}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${docCategoryBadgeClass(item.doc_category)}`}
                    >
                      {item.doc_category}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-700">
                    {item.project_id ? (
                      <span className="font-mono text-[11px] text-gray-500">
                        {item.project_id}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800">
                        ICC-wide
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-700">
                    {item.module_id ? (
                      <span className="font-mono text-[11px] text-gray-500">
                        {item.module_id}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                        project-level
                      </span>
                    )}
                  </td>
                  <td className="max-w-xs truncate px-4 py-2 font-mono text-[11px] text-gray-700">
                    <span title={item.file_path}>{item.file_path}</span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {item.qdrant_point_id ? (
                      <span
                        className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 font-medium text-emerald-800"
                        title={`Qdrant point id: ${item.qdrant_point_id}`}
                      >
                        {formatTimestamp(item.indexed_at)}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 font-medium text-gray-700">
                        not indexed
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

interface KbDocumentDetailProps {
  row: KbDocumentRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function KbDocumentDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: KbDocumentDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading KB document…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">KB document not found.</p>
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
            Category
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${docCategoryBadgeClass(row.doc_category)}`}
            >
              {row.doc_category}
            </span>
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Title
          </dt>
          <dd className="text-sm text-gray-900">{row.title}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            File path
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.file_path}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Project ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.project_id ?? "— (ICC-wide document)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Module ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.module_id ?? "— (project-level / ICC-wide)"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Qdrant collection
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.qdrant_collection)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Qdrant point ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {formatOptional(row.qdrant_point_id)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Indexed at
          </dt>
          <dd className="text-sm text-gray-900">
            {row.indexed_at ? (
              <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                {formatTimestamp(row.indexed_at)}
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                not indexed
              </span>
            )}
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

interface KbDocumentFormProps {
  form: KbDocumentFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: KbDocumentFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function KbDocumentForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: KbDocumentFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<KbDocumentFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading KB document…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit KB document" : "Create KB document"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="title"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Title
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; max 500 chars)
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
            placeholder="Human-readable document title"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="file_path"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            File path
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; absolute path on the ANDROS filesystem)
            </span>
          </label>
          <input
            id="file_path"
            type="text"
            value={form.file_path}
            onChange={(event) => patch({ file_path: event.target.value })}
            required
            minLength={1}
            placeholder="/home/icc/knowledge/…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="doc_category"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Category
            <span className="ml-1 text-xs font-normal text-gray-500">
              (immutable after create)
            </span>
          </label>
          <select
            id="doc_category"
            value={form.doc_category}
            onChange={(event) =>
              patch({
                doc_category: event.target.value as KbDocumentCategory,
              })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          >
            {DOC_CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="indexed_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Indexed at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; typically populated by a subsequent Qdrant
              indexing run)
            </span>
          </label>
          <input
            id="indexed_at"
            type="datetime-local"
            value={form.indexed_at}
            onChange={(event) => patch({ indexed_at: event.target.value })}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="project_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional UUID; FK → projects, ON DELETE CASCADE; blank =
              ICC-wide document; immutable after create)
            </span>
          </label>
          <input
            id="project_id"
            type="text"
            value={form.project_id}
            onChange={(event) => patch({ project_id: event.target.value })}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the project UUID this document belongs to, or leave blank for an ICC-wide document."
            placeholder="blank = ICC-wide document"
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
              blank = project-level or ICC-wide document)
            </span>
          </label>
          <input
            id="module_id"
            type="text"
            value={form.module_id}
            onChange={(event) => patch({ module_id: event.target.value })}
            pattern={UUID_PATTERN}
            title="Enter the project module UUID this document is scoped to, or leave blank for a project-level / ICC-wide document."
            placeholder="blank = project-level / ICC-wide"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="qdrant_collection"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Qdrant collection
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; max 100 chars; populated by a subsequent indexing
              run)
            </span>
          </label>
          <input
            id="qdrant_collection"
            type="text"
            value={form.qdrant_collection}
            onChange={(event) =>
              patch({ qdrant_collection: event.target.value })
            }
            maxLength={100}
            placeholder="blank = not yet indexed"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="qdrant_point_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Qdrant point ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; max 100 chars; populated by a subsequent indexing
              run)
            </span>
          </label>
          <input
            id="qdrant_point_id"
            type="text"
            value={form.qdrant_point_id}
            onChange={(event) =>
              patch({ qdrant_point_id: event.target.value })
            }
            maxLength={100}
            placeholder="blank = not yet indexed"
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

export default KbDocumentPage;
