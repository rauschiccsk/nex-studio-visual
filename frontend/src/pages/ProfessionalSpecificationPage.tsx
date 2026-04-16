/**
 * ProfessionalSpecification admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ProfessionalSpecification CRUD surface against the
 * backend REST router mounted at
 * ``/api/v1/professional-specifications`` (see
 * ``backend/api/routes/professional_specifications.py`` and its prefix
 * in ``backend/main.py``). A ``professional_specifications`` row is the
 * AI-generated, structured markdown derived from a customer-submitted
 * :class:`~backend.db.models.specifications.RawSpecification` (DESIGN.md
 * §1.8 ProfessionalSpecification, §2 ``professional_specifications``
 * table, §6.5 Specification Pipeline). Once approved
 * (``approved_by`` non-null) the document unlocks downstream DESIGN.md
 * generation (DESIGN.md §9 / §10 pipeline gating: ``approved_by`` must
 * be non-null before ``design-documents/generate`` can be triggered).
 *
 * Like the other Feat 6 admin pages (``RawSpecificationPage``,
 * ``DesignDocumentPage``, ``ProjectModulePage``, …) this surface is
 * deliberately self-contained rather than reaching for a global Zustand
 * store: per DESIGN.md § 3.3 ``specStore`` backs the end-user
 * ``SpecificationPage`` / ``SpecificationViewer`` UI, which is a
 * distinct concern from a per-row administrative CRUD editor. When the
 * store grows dedicated admin actions in a later feat this page can
 * switch over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id``,
 *     ``raw_spec_id``, ``approved_by`` and/or ``version``, with
 *     row-level "View", "Edit" and "Delete" actions. Results are
 *     ordered by ``created_at DESC`` (newest version first) on the
 *     backend — matching the ``SpecificationViewer`` "version-history,
 *     latest regeneration on top" UI convention (DESIGN.md §3.1).
 *   - ``detail`` — read-only view of a single professional
 *     specification: primary key, ``raw_spec_id``, ``project_id``,
 *     ``version``, ``approved_by``, ``approved_at``, full markdown
 *     ``content`` and audit timestamps.
 *   - ``create`` — form that ``POST``s a new professional specification.
 *     ``raw_spec_id``, ``project_id`` and ``content`` are required;
 *     ``version`` defaults to ``1`` (Pydantic / DB ``server_default``);
 *     ``approved_by`` / ``approved_at`` are normally left blank on
 *     create — a specification is approved via a subsequent ``PATCH``
 *     by a user with the ``ri`` role (DESIGN.md §9 business rule).
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``content``, ``version``, ``approved_by``, ``approved_at``).
 *     ``raw_spec_id`` and ``project_id`` are rendered read-only — a
 *     professional specification belongs to exactly one project and is
 *     derived from exactly one raw specification for its lifetime
 *     (regenerations are new rows with an incremented ``version``, not
 *     a reassignment). :class:`ProfessionalSpecificationUpdate`
 *     deliberately omits both columns (see
 *     ``backend/schemas/professional_specification.py``). When
 *     ``approved_by`` transitions from unset to a user UUID and the
 *     caller leaves ``approved_at`` blank, the backend stamps
 *     ``approved_at = now()`` automatically — mirroring the
 *     ``approved_at`` auto-stamp on design documents, the
 *     ``resolved_at`` auto-stamp on bugs and the ``closed_at``
 *     auto-stamp on Architect sessions.
 *
 * ``DELETE`` is a hard delete. ``professional_specifications`` has
 * **no inbound foreign keys** — no other table references it — so no
 * RESTRICT dependency check is required. In normal operation
 * professional specifications are retained as version history
 * (DESIGN.md §3.1 ``SpecificationPage`` / ``SpecificationViewer``);
 * delete is reserved for test fixtures / admin redaction tooling where
 * the generated document itself must go. The outbound FKs
 * ``project_id`` (``ON DELETE CASCADE``), ``raw_spec_id`` (``ON DELETE
 * CASCADE``) and ``approved_by`` (``ON DELETE RESTRICT``) keep the row
 * self-consistent when the parent rows change. The confirmation dialog
 * warns the user.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / constraint failures to HTTP 422 and missing rows to
 * HTTP 404; all are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/professional-specifications``
 * alongside the other Feat 6 CRUD surfaces (``/admin/users``,
 * ``/admin/projects``, ``/admin/raw-specifications``,
 * ``/admin/design-documents``, ``/admin/kb-documents``,
 * ``/admin/module-dependencies``, …). It is distinct from
 * ``SpecificationPage`` (the end-user ``SpecificationViewer`` surface
 * at ``/projects/:slug/spec``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  ProfessionalSpecificationCreate,
  ProfessionalSpecificationRead,
  ProfessionalSpecificationUpdate,
} from "../types";

/** REST prefix for the ProfessionalSpecification router (see backend/main.py). */
const ENDPOINT = "/professional-specifications";

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
 * ``textarea`` / ``number`` / ``datetime-local`` input ``value`` is
 * always a string. UUID inputs enforce the canonical shape via the
 * ``pattern`` attribute and the backend rejects malformed values with
 * HTTP 422.
 */
interface ProfessionalSpecificationFormState {
  raw_spec_id: string;
  project_id: string;
  content: string;
  version: string;
  approved_by: string;
  approved_at: string;
}

/** Fresh-form defaults for the create mode — ``version`` mirrors the DB ``server_default``. */
const EMPTY_FORM: ProfessionalSpecificationFormState = {
  raw_spec_id: "",
  project_id: "",
  content: "",
  version: "1",
  approved_by: "",
  approved_at: "",
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
 * Returns ``fallback`` for blank input so the column falls back to the
 * DB ``server_default`` equivalent. Non-integer or non-positive inputs
 * also fall through to ``fallback`` — the backend ``Field(ge=1)`` would
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

function ProfessionalSpecificationPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ProfessionalSpecificationRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");
  const [rawSpecFilter, setRawSpecFilter] = useState("");
  const [approvedByFilter, setApprovedByFilter] = useState("");
  const [versionFilter, setVersionFilter] = useState("");

  const [detail, setDetail] = useState<ProfessionalSpecificationRead | null>(
    null,
  );
  const [form, setForm] =
    useState<ProfessionalSpecificationFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Convert the version filter to a number only if it parses cleanly;
      // blank / garbage falls through as ``undefined`` and is omitted from
      // the request. The backend rejects ``version < 1`` with HTTP 422.
      const versionTrimmed = versionFilter.trim();
      let versionParam: number | undefined;
      if (versionTrimmed.length > 0) {
        const parsed = Number.parseInt(versionTrimmed, 10);
        if (!Number.isNaN(parsed) && parsed >= 1) {
          versionParam = parsed;
        }
      }
      const response = await api.get<
        PaginatedResponse<ProfessionalSpecificationRead>
      >(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          project_id: projectFilter.trim() || undefined,
          raw_spec_id: rawSpecFilter.trim() || undefined,
          approved_by: approvedByFilter.trim() || undefined,
          version: versionParam,
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load professional specifications.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [
    skip,
    projectFilter,
    rawSpecFilter,
    approvedByFilter,
    versionFilter,
  ]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ProfessionalSpecificationRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load professional specification.";
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
        const row = await api.get<ProfessionalSpecificationRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setDetail(row);
        setForm({
          raw_spec_id: row.raw_spec_id,
          project_id: row.project_id,
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
            : "Failed to load professional specification.";
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
        "Delete this professional specification? This is a hard delete — no other tables reference professional_specifications, so no dependency check applies. Specifications are normally retained as version history (SpecificationViewer); delete is reserved for redaction / test-fixture cleanup.",
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
          : "Failed to delete professional specification.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const approvedBy = form.approved_by.trim();
      const payload: ProfessionalSpecificationCreate = {
        raw_spec_id: form.raw_spec_id.trim(),
        project_id: form.project_id.trim(),
        content: form.content,
        version: parsePositiveIntOrDefault(form.version, 1),
        approved_by: approvedBy ? approvedBy : null,
        approved_at: parseOptionalDateTime(form.approved_at),
      };
      await api.post<ProfessionalSpecificationRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create professional specification.";
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
      // ``raw_spec_id`` and ``project_id`` are immutable (see
      // backend/schemas/professional_specification.py —
      // ProfessionalSpecificationUpdate deliberately omits them). We
      // only send the mutable fields.
      //
      // PATCH semantics on the backend: fields that are ``None`` are
      // treated as "leave unchanged" (``approved_by`` and
      // ``approved_at`` are therefore sticky once set). We send every
      // mutable field unconditionally since the form always has them
      // populated from the seeded row; blank approval input is
      // normalised to ``null`` here so the payload shape stays
      // consistent with the create path. Explicit "un-approve"
      // transitions are admin-only corrections and not expressible
      // through this UI — matching the backend service contract.
      const approvedBy = form.approved_by.trim();
      const payload: ProfessionalSpecificationUpdate = {
        content: form.content,
        version: parsePositiveIntOrDefault(form.version, 1),
        approved_by: approvedBy ? approvedBy : null,
        approved_at: parseOptionalDateTime(form.approved_at),
      };
      await api.patch<ProfessionalSpecificationRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update professional specification.";
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
            Professional specifications
          </h2>
          <p className="text-sm text-gray-600">
            AI-generated, structured markdown derived from a raw
            customer specification (DESIGN.md §1.8, §6.5 Specification
            Pipeline). Multiple rows sharing
            ``(project_id, raw_spec_id)`` represent regeneration
            history (one row per ``version``); newest is shown first.
            Approval (``approved_by`` non-null) unlocks DESIGN.md
            generation (DESIGN.md §9 / §10 pipeline gating). Delete is a
            hard delete reserved for redaction / test fixtures —
            specifications are normally retained as version history.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new professional specification"
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
        <ProfessionalSpecificationList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
          }}
          rawSpecFilter={rawSpecFilter}
          onRawSpecFilterChange={(value) => {
            setSkip(0);
            setRawSpecFilter(value);
          }}
          approvedByFilter={approvedByFilter}
          onApprovedByFilterChange={(value) => {
            setSkip(0);
            setApprovedByFilter(value);
          }}
          versionFilter={versionFilter}
          onVersionFilterChange={(value) => {
            setSkip(0);
            setVersionFilter(value);
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
        <ProfessionalSpecificationDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ProfessionalSpecificationForm
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

interface ProfessionalSpecificationListProps {
  items: ProfessionalSpecificationRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  rawSpecFilter: string;
  onRawSpecFilterChange: (value: string) => void;
  approvedByFilter: string;
  onApprovedByFilterChange: (value: string) => void;
  versionFilter: string;
  onVersionFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ProfessionalSpecificationList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  rawSpecFilter,
  onRawSpecFilterChange,
  approvedByFilter,
  onApprovedByFilterChange,
  versionFilter,
  onVersionFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ProfessionalSpecificationListProps) {
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
            htmlFor="raw-spec-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Raw spec ID
          </label>
          <input
            id="raw-spec-filter"
            type="text"
            value={rawSpecFilter}
            onChange={(event) => onRawSpecFilterChange(event.target.value)}
            pattern={UUID_PATTERN}
            title="Enter a canonical UUID to restrict to professional specifications derived from this raw specification, or leave blank to include all raw specs."
            placeholder="UUID — blank = all raw specs"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
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
            title="Enter a canonical user UUID to filter by approver (typically ri-role), or leave blank to show specifications from all approvers (including unapproved)."
            placeholder="User UUID — blank = all"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="version-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Version
          </label>
          <input
            id="version-filter"
            type="number"
            min={1}
            step={1}
            value={versionFilter}
            onChange={(event) => onVersionFilterChange(event.target.value)}
            title="Filter by version number (≥ 1) — fetch a specific version from the regeneration history. Blank = all versions."
            placeholder="blank = all"
            className="w-28 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
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
                Raw spec
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
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading professional specifications…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No professional specifications match the current filter.
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
                    {item.raw_spec_id}
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

interface ProfessionalSpecificationDetailProps {
  row: ProfessionalSpecificationRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ProfessionalSpecificationDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ProfessionalSpecificationDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading professional specification…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">
          Professional specification not found.
        </p>
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
            Raw spec ID
            <span className="ml-1 text-xs font-normal text-gray-400">
              (immutable)
            </span>
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.raw_spec_id}
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
            Status
          </dt>
          <dd>
            {row.approved_by ? (
              <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                approved
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-700">
                pending
              </span>
            )}
          </dd>
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

interface ProfessionalSpecificationFormProps {
  form: ProfessionalSpecificationFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ProfessionalSpecificationFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ProfessionalSpecificationForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ProfessionalSpecificationFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ProfessionalSpecificationFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading professional specification…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit
          ? "Edit professional specification"
          : "Create professional specification"}
      </h3>
      <p className="text-sm text-gray-600">
        ``raw_spec_id`` and ``project_id`` are immutable after create —
        a professional specification belongs to exactly one project and
        is derived from exactly one raw specification for its lifetime
        (regenerations are new rows with an incremented ``version``).
        ``version`` defaults to ``1``. ``approved_by`` /
        ``approved_at`` are typically left blank on create — approval
        happens via a subsequent edit by a user with the ``ri`` role and
        unlocks DESIGN.md generation (DESIGN.md §9 / §10 pipeline
        gating).
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
            htmlFor="raw_spec_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Raw spec ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → raw_specifications, ON DELETE CASCADE;
              immutable after create)
            </span>
          </label>
          <input
            id="raw_spec_id"
            type="text"
            value={form.raw_spec_id}
            onChange={(event) => patch({ raw_spec_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Enter the raw-specification UUID this professional specification was derived from."
            placeholder="e.g. 7fcd8c42-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
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
            title="Enter the UUID of the approver (typically a ri-role user), or leave blank for a pending-approval specification."
            placeholder="blank = pending approval"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="approved_at"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Approved at
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; stamped automatically when approved_by
              transitions from blank to a user UUID via PATCH)
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

        <div className="sm:col-span-2">
          <label
            htmlFor="content"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Content
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required — structured markdown body: business
              requirements, actors, use cases, constraints,
              out-of-scope items)
            </span>
          </label>
          <textarea
            id="content"
            value={form.content}
            onChange={(event) => patch({ content: event.target.value })}
            required
            rows={14}
            minLength={1}
            placeholder="# Professional specification…"
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

export default ProfessionalSpecificationPage;
