/**
 * GuardianReview admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 GuardianReview CRUD surface against the backend REST
 * router mounted at ``/api/v1/guardian-reviews`` (see
 * ``backend/api/routes/guardian_reviews.py``). One ``guardian_reviews``
 * row is the Layer 1 / 2 / 3 review result attached to a delegation
 * (DESIGN.md §1.21 GuardianReview, §1.8 ``guardian_reviews`` table)
 * and is the raw data behind the ``GuardianPanel`` UI (DESIGN.md §3.1).
 *
 * Like the other Feat 6 admin pages (``DelegationAdminPage``,
 * ``ExecutionLogPage``, ``AutoFixAttemptPage``, ``FeatPage``,
 * ``TaskAdminPage``, …) this surface is deliberately self-contained
 * rather than reaching for a global Zustand store: per DESIGN.md §3.3
 * the review data is consumed by the per-delegation ``GuardianPanel``
 * live-output surface, which is a distinct concern from a per-row
 * administrative CRUD editor. When the stores grow dedicated admin
 * actions in a later feat this page can switch over without changing
 * its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``delegation_id`` /
 *     ``layer`` / ``risk_level`` / ``passed``, with row-level "View",
 *     "Edit" and "Delete" actions. Results are ordered by
 *     ``created_at DESC`` so the most recently recorded reviews appear
 *     first — matching the service-layer ordering owned by
 *     ``backend/services/guardian_review.py`` and the reporting / audit-
 *     log conventions used throughout the UI.
 *   - ``detail`` — read-only view of a single Guardian review: primary
 *     key, parent reference (``delegation_id``), the pipeline layer
 *     (``layer1 | layer2 | layer3``), the maximum ``risk_level`` of the
 *     changed files, the blocking flag (``passed``), the JSONB
 *     ``findings`` array (pretty-printed), the wall-clock
 *     ``duration_ms`` and the ``created_at`` timestamp. There is **no**
 *     ``updated_at`` column — per DESIGN.md §1.21 reviews are
 *     conceptually immutable (mutations are limited to post-hoc
 *     precedent filtering and the audit trail is the delegation history
 *     itself).
 *   - ``create`` — form that ``POST``s a new Guardian review.
 *     ``delegation_id``, ``layer`` and ``risk_level`` are required;
 *     ``findings`` defaults to ``[]`` (server default) and ``passed``
 *     defaults to ``false`` (server default) when omitted;
 *     ``duration_ms`` is optional. Invalid / missing FK references
 *     (``delegation_id``) are rejected by the DB-level FK and surface
 *     as HTTP 422.
 *   - ``edit``   — form that ``PATCH``es the mutable fields
 *     (``risk_level``, ``findings``, ``passed``, ``duration_ms``).
 *     ``delegation_id`` and ``layer`` are rendered read-only — the
 *     review's parent reference and pipeline layer are immutable by
 *     design (:class:`GuardianReviewUpdate` deliberately omits both,
 *     see ``backend/schemas/guardian.py``) because a review for
 *     ``layer1`` cannot become a ``layer2`` review (DESIGN.md §1.21)
 *     and the DB handles orphaning automatically via ``ON DELETE
 *     CASCADE`` on ``delegation_id``. The primary edit use case is
 *     post-hoc precedent filtering: applying a new ``allow`` precedent
 *     may flip ``passed`` from ``false`` to ``true`` and prune matched
 *     entries from ``findings``.
 *
 * ``DELETE`` is a hard delete. ``guardian_reviews`` has no inbound
 * foreign keys, so no RESTRICT dependency check is required — simply
 * drop the row. Routine operation retains the full review history for
 * reporting / audit (DESIGN.md §1.21); delete is reserved for test-
 * fixture cleanup and admin redaction. Deleting the parent
 * ``delegations`` row cascades automatically via
 * ``guardian_reviews.delegation_id ON DELETE CASCADE`` — that is the
 * usual path for wiping a delegation's reviews.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / layer / risk-level values / constraint failures to
 * HTTP 422 and they are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/guardian-reviews`` alongside the
 * other Feat 6 CRUD surfaces (``/admin/guardian-precedents``,
 * ``/admin/users``, ``/admin/projects``, ``/admin/bugs``,
 * ``/admin/bug-fix-tasks``, ``/admin/tasks``, ``/admin/feats``,
 * ``/admin/epics``, ``/admin/auto-fix-attempts``,
 * ``/admin/delegations``, ``/admin/execution-logs``, …). It is distinct
 * from the end-user ``GuardianPanel`` surface (DESIGN.md §3.1) — that
 * view renders Layer 1/2/3 results inline inside the live delegation
 * output and consumes the same REST endpoint read-only.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  GuardianFinding,
  GuardianReviewCreate,
  GuardianReviewLayer,
  GuardianReviewRead,
  GuardianReviewRiskLevel,
  GuardianReviewUpdate,
  PaginatedResponse,
} from "../types";

/** REST prefix for the GuardianReview router (see backend/main.py). */
const ENDPOINT = "/guardian-reviews";

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
 * ``number`` / ``select`` / ``checkbox`` input ``value`` is always a
 * string (booleans read via ``checked``). UUID inputs enforce the
 * canonical shape via the ``pattern`` attribute and the backend rejects
 * malformed values with HTTP 422. ``layer`` and ``risk_level`` are cast
 * to their literal unions at submit time.
 *
 * ``findings`` is captured as a raw JSON string so the operator can
 * edit the full JSONB array as free-form text; it is parsed via
 * ``JSON.parse`` at submit time and validated to be an array (the DB
 * column is a JSONB array of finding objects). ``duration_ms`` is
 * captured as a string and converted at submit time; blank = unset
 * (backend default on create; "leave unchanged" on edit).
 */
interface GuardianReviewFormState {
  delegation_id: string;
  layer: GuardianReviewLayer;
  risk_level: GuardianReviewRiskLevel;
  findings: string;
  passed: boolean;
  duration_ms: string;
}

/**
 * Selectable layers; mirrors the ``GuardianReviewLayer`` literal union
 * and the ``ck_guardian_reviews_layer`` DB CHECK constraint
 * (``layer1 | layer2 | layer3``).
 */
const LAYER_OPTIONS: readonly GuardianReviewLayer[] = [
  "layer1",
  "layer2",
  "layer3",
] as const;

/**
 * Selectable risk levels; mirrors the ``GuardianReviewRiskLevel``
 * literal union and the ``ck_guardian_reviews_risk_level`` DB CHECK
 * constraint (``low | medium | high | critical``).
 */
const RISK_LEVEL_OPTIONS: readonly GuardianReviewRiskLevel[] = [
  "low",
  "medium",
  "high",
  "critical",
] as const;

/**
 * Fresh-form defaults for the create mode.
 *
 * ``layer`` defaults to ``layer1`` — the entry point of the Guardian
 * pipeline — and ``risk_level`` to ``low`` — the least-restrictive
 * bucket. The unhappy paths are explicitly selected by the operator so
 * the severity fields cannot silently hide a riskier review.
 * ``findings`` defaults to the textual representation of the empty
 * array (matching the DB ``server_default='[]'``) and ``passed``
 * mirrors the DB ``server_default='false'``.
 */
const EMPTY_FORM: GuardianReviewFormState = {
  delegation_id: "",
  layer: "layer1",
  risk_level: "low",
  findings: "[]",
  passed: false,
  duration_ms: "",
};

/** Tailwind helper for layer pills. */
function layerBadgeClass(value: GuardianReviewLayer): string {
  switch (value) {
    case "layer1":
      return "bg-sky-100 text-sky-800";
    case "layer2":
      return "bg-indigo-100 text-indigo-800";
    case "layer3":
      return "bg-purple-100 text-purple-800";
  }
}

/** Tailwind helper for risk-level pills. */
function riskBadgeClass(value: GuardianReviewRiskLevel): string {
  switch (value) {
    case "low":
      return "bg-emerald-100 text-emerald-800";
    case "medium":
      return "bg-amber-100 text-amber-800";
    case "high":
      return "bg-orange-100 text-orange-800";
    case "critical":
      return "bg-red-100 text-red-800";
  }
}

/** Tailwind helper for the passed / blocking pill. */
function passedBadgeClass(value: boolean): string {
  return value
    ? "bg-emerald-100 text-emerald-800"
    : "bg-red-100 text-red-800";
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

/** Render an optional integer field for the detail / list views. */
function formatOptionalNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

/** Render the findings count label for the list / detail views. */
function formatFindingsCount(findings: GuardianFinding[]): string {
  const count = findings.length;
  return `${count} finding${count === 1 ? "" : "s"}`;
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
 * Parse a user-entered integer string into ``number | null``. Blank =
 * ``null`` (server default on create; "leave unchanged" on edit).
 * Returns ``NaN`` for malformed input so the caller can raise an
 * explicit validation error instead of silently sending garbage.
 */
function parseOptionalInt(raw: string): number | null | typeof NaN {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
    return Number.NaN;
  }
  return value;
}

/**
 * Parse a user-entered JSON blob into a ``GuardianFinding[]``. Throws
 * an :class:`Error` with a human-readable message on parse failure or
 * when the decoded value is not an array of objects — the backend
 * column is a JSONB array of finding objects so anything else is
 * rejected upfront to avoid a 422 round-trip.
 */
function parseFindings(raw: string): GuardianFinding[] {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    return [];
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (exc) {
    const detail = exc instanceof Error ? exc.message : String(exc);
    throw new Error(`Findings is not valid JSON: ${detail}`);
  }
  if (!Array.isArray(parsed)) {
    throw new Error("Findings must be a JSON array of finding objects.");
  }
  for (const entry of parsed) {
    if (
      entry === null ||
      typeof entry !== "object" ||
      Array.isArray(entry)
    ) {
      throw new Error(
        "Each finding must be a JSON object (severity, rule, file_path, …).",
      );
    }
  }
  return parsed as GuardianFinding[];
}

/** Pretty-print a JSONB findings value for display / edit seeding. */
function formatFindingsJson(findings: GuardianFinding[]): string {
  return JSON.stringify(findings, null, 2);
}

function GuardianReviewPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<GuardianReviewRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [delegationFilter, setDelegationFilter] = useState("");
  const [layerFilter, setLayerFilter] = useState<GuardianReviewLayer | "">("");
  const [riskLevelFilter, setRiskLevelFilter] = useState<
    GuardianReviewRiskLevel | ""
  >("");
  const [passedFilter, setPassedFilter] = useState<"" | "true" | "false">("");

  const [detail, setDetail] = useState<GuardianReviewRead | null>(null);
  const [form, setForm] = useState<GuardianReviewFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<GuardianReviewRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            delegation_id: delegationFilter.trim() || undefined,
            layer: layerFilter || undefined,
            risk_level: riskLevelFilter || undefined,
            passed:
              passedFilter === "" ? undefined : passedFilter === "true",
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load Guardian reviews.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, delegationFilter, layerFilter, riskLevelFilter, passedFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<GuardianReviewRead>(
        `${ENDPOINT}/${id}`,
      );
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load Guardian review.";
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
        const row = await api.get<GuardianReviewRead>(
          `${ENDPOINT}/${mode.id}`,
        );
        if (cancelled) {
          return;
        }
        setForm({
          delegation_id: row.delegation_id,
          layer: row.layer,
          risk_level: row.risk_level,
          findings: formatFindingsJson(row.findings),
          passed: row.passed,
          duration_ms:
            row.duration_ms === null ? "" : String(row.duration_ms),
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load Guardian review.";
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
        "Delete this Guardian review? This is a hard delete. guardian_reviews has no inbound foreign keys, so no RESTRICT dependency check is required. Routine operation retains the full review history for reporting / audit; delete is reserved for test-fixture cleanup and admin redaction. Proceed?",
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
          : "Failed to delete Guardian review.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const delegation = form.delegation_id.trim();
      if (delegation.length === 0) {
        throw new Error("Delegation ID is required.");
      }
      const findings = parseFindings(form.findings);
      const duration = parseOptionalInt(form.duration_ms);
      if (Number.isNaN(duration)) {
        throw new Error("Duration (ms) must be a non-negative integer.");
      }
      const payload: GuardianReviewCreate = {
        delegation_id: delegation,
        layer: form.layer,
        risk_level: form.risk_level,
        findings,
        passed: form.passed,
        duration_ms: duration as number | null,
      };
      await api.post<GuardianReviewRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to create Guardian review.";
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
      // Only the mutable metric / precedent-filter fields are sent —
      // parent FK (``delegation_id``) and pipeline ``layer`` are
      // immutable (see backend/schemas/guardian.py —
      // GuardianReviewUpdate deliberately omits them). ``findings`` is
      // always sent because the JSONB column has no "omit = keep"
      // ambiguity here (the empty array is a valid outcome of precedent
      // filtering). ``passed`` is always sent because it is a boolean
      // and the precedent-filter job must be able to flip it freely.
      const findings = parseFindings(form.findings);
      const duration = parseOptionalInt(form.duration_ms);
      if (Number.isNaN(duration)) {
        throw new Error("Duration (ms) must be a non-negative integer.");
      }
      const payload: GuardianReviewUpdate = {
        risk_level: form.risk_level,
        findings,
        passed: form.passed,
      };
      if (duration !== null) {
        payload.duration_ms = duration as number;
      }
      await api.patch<GuardianReviewRead>(
        `${ENDPOINT}/${mode.id}`,
        payload,
      );
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : exc instanceof Error
            ? exc.message
            : "Failed to update Guardian review.";
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
            Guardian reviews
          </h2>
          <p className="text-sm text-gray-600">
            Layer 1 / 2 / 3 review results attached to a delegation
            (DESIGN.md §1.21 / §1.8 ``guardian_reviews`` table). A review
            belongs to exactly one delegation (``ON DELETE CASCADE``).
            Ordered by ``created_at DESC`` (most recent first). Reviews
            are conceptually immutable, but ``risk_level``, ``findings``,
            ``passed`` and ``duration_ms`` remain updatable to support
            post-hoc precedent filtering — applying a new ``allow``
            precedent may flip ``passed`` to ``true`` and prune matched
            entries from ``findings``. Delete is a hard delete; routine
            operation retains the full review history.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new Guardian review"
          >
            New Guardian review
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
        <GuardianReviewList
          items={items}
          total={total}
          isLoading={isLoading}
          delegationFilter={delegationFilter}
          onDelegationFilterChange={(value) => {
            setSkip(0);
            setDelegationFilter(value);
          }}
          layerFilter={layerFilter}
          onLayerFilterChange={(value) => {
            setSkip(0);
            setLayerFilter(value);
          }}
          riskLevelFilter={riskLevelFilter}
          onRiskLevelFilterChange={(value) => {
            setSkip(0);
            setRiskLevelFilter(value);
          }}
          passedFilter={passedFilter}
          onPassedFilterChange={(value) => {
            setSkip(0);
            setPassedFilter(value);
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
        <GuardianReviewDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <GuardianReviewForm
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

interface GuardianReviewListProps {
  items: GuardianReviewRead[];
  total: number;
  isLoading: boolean;
  delegationFilter: string;
  onDelegationFilterChange: (value: string) => void;
  layerFilter: GuardianReviewLayer | "";
  onLayerFilterChange: (value: GuardianReviewLayer | "") => void;
  riskLevelFilter: GuardianReviewRiskLevel | "";
  onRiskLevelFilterChange: (value: GuardianReviewRiskLevel | "") => void;
  passedFilter: "" | "true" | "false";
  onPassedFilterChange: (value: "" | "true" | "false") => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function GuardianReviewList({
  items,
  total,
  isLoading,
  delegationFilter,
  onDelegationFilterChange,
  layerFilter,
  onLayerFilterChange,
  riskLevelFilter,
  onRiskLevelFilterChange,
  passedFilter,
  onPassedFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: GuardianReviewListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
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
            title="Enter a canonical UUID to show reviews for a specific delegation. Blank = all."
            placeholder="UUID — blank = all"
            className="w-56 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="layer-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Layer
          </label>
          <select
            id="layer-filter"
            value={layerFilter}
            onChange={(event) =>
              onLayerFilterChange(
                event.target.value as GuardianReviewLayer | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {LAYER_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="risk-level-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Risk level
          </label>
          <select
            id="risk-level-filter"
            value={riskLevelFilter}
            onChange={(event) =>
              onRiskLevelFilterChange(
                event.target.value as GuardianReviewRiskLevel | "",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            {RISK_LEVEL_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col">
          <label
            htmlFor="passed-filter"
            className="mb-1 text-sm font-medium text-gray-700"
          >
            Passed
          </label>
          <select
            id="passed-filter"
            value={passedFilter}
            onChange={(event) =>
              onPassedFilterChange(
                event.target.value as "" | "true" | "false",
              )
            }
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            <option value="">All</option>
            <option value="true">Passed</option>
            <option value="false">Blocking</option>
          </select>
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} Guardian review{total === 1 ? "" : "s"} total
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
                Layer
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Risk level
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Passed
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Delegation
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="JSONB array of finding objects."
              >
                Findings
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
                title="Wall-clock execution time of the review in milliseconds."
              >
                Duration (ms)
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
                  Loading Guardian reviews…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No Guardian reviews match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${layerBadgeClass(item.layer)}`}
                    >
                      {item.layer}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${riskBadgeClass(item.risk_level)}`}
                    >
                      {item.risk_level}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${passedBadgeClass(item.passed)}`}
                    >
                      {item.passed ? "passed" : "blocking"}
                    </span>
                  </td>
                  <td
                    className="px-4 py-2 font-mono text-[11px] text-gray-500"
                    title={item.delegation_id}
                  >
                    {item.delegation_id.slice(0, 8)}…
                  </td>
                  <td className="px-4 py-2 text-right text-sm text-gray-900">
                    {formatFindingsCount(item.findings)}
                  </td>
                  <td className="px-4 py-2 text-right text-sm text-gray-900">
                    {formatOptionalNumber(item.duration_ms)}
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

interface GuardianReviewDetailProps {
  row: GuardianReviewRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function GuardianReviewDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: GuardianReviewDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading Guardian review…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Guardian review not found.</p>
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
            Guardian review ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {row.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Layer
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${layerBadgeClass(row.layer)}`}
            >
              {row.layer}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Risk level
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${riskBadgeClass(row.risk_level)}`}
            >
              {row.risk_level}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Passed
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${passedBadgeClass(row.passed)}`}
            >
              {row.passed ? "passed" : "blocking"}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Duration (ms)
          </dt>
          <dd className="text-sm text-gray-900">
            {formatOptionalNumber(row.duration_ms)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Delegation ID
          </dt>
          <dd className="break-all font-mono text-xs text-gray-900">
            {row.delegation_id}
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
            Findings
          </dt>
          <dd className="text-sm text-gray-900">
            {formatFindingsCount(row.findings)}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Findings (JSON)
          </dt>
          <dd>
            <pre className="max-h-96 overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-xs text-gray-800">
              {formatFindingsJson(row.findings)}
            </pre>
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

interface GuardianReviewFormProps {
  form: GuardianReviewFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: GuardianReviewFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function GuardianReviewForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: GuardianReviewFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<GuardianReviewFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading Guardian review…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit Guardian review" : "Create Guardian review"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="delegation_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Delegation ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required UUID; FK → delegations, ON DELETE CASCADE;
              immutable after create)
            </span>
          </label>
          <input
            id="delegation_id"
            type="text"
            value={form.delegation_id}
            onChange={(event) => patch({ delegation_id: event.target.value })}
            required={!isEdit}
            readOnly={isEdit}
            pattern={UUID_PATTERN}
            title="Delegation this Guardian review belongs to."
            placeholder="UUID"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="layer"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Layer
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; layer1 | layer2 | layer3; immutable after
              create)
            </span>
          </label>
          <select
            id="layer"
            value={form.layer}
            onChange={(event) =>
              patch({ layer: event.target.value as GuardianReviewLayer })
            }
            disabled={isEdit}
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          >
            {LAYER_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="risk_level"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Risk level
            <span className="ml-1 text-xs font-normal text-gray-500">
              (required; low | medium | high | critical)
            </span>
          </label>
          <select
            id="risk_level"
            value={form.risk_level}
            onChange={(event) =>
              patch({
                risk_level: event.target.value as GuardianReviewRiskLevel,
              })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {RISK_LEVEL_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-end">
          <label
            htmlFor="passed"
            className="inline-flex items-center gap-2 text-sm font-medium text-gray-700"
          >
            <input
              id="passed"
              type="checkbox"
              checked={form.passed}
              onChange={(event) => patch({ passed: event.target.checked })}
              className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            Passed
            <span className="text-xs font-normal text-gray-500">
              (server default ``false``; flipped to ``true`` after
              precedent filtering prunes all blocking findings)
            </span>
          </label>
        </div>

        <div>
          <label
            htmlFor="duration_ms"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Duration (ms)
            <span className="ml-1 text-xs font-normal text-gray-500">
              (optional; non-negative integer; blank = unset /
              unchanged)
            </span>
          </label>
          <input
            id="duration_ms"
            type="number"
            min={0}
            step={1}
            value={form.duration_ms}
            onChange={(event) => patch({ duration_ms: event.target.value })}
            placeholder="blank = unset"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="findings"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Findings
            <span className="ml-1 text-xs font-normal text-gray-500">
              (JSONB array of finding objects — severity, rule,
              file_path, line_range, description, suggestion,
              confidence; defaults to ``[]``)
            </span>
          </label>
          <textarea
            id="findings"
            value={form.findings}
            onChange={(event) => patch({ findings: event.target.value })}
            rows={12}
            spellCheck={false}
            placeholder='[]'
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

export default GuardianReviewPage;
