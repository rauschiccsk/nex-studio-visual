/**
 * ReportConfig admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 ReportConfig CRUD surface against the backend REST
 * router mounted at ``/api/v1/report-configs`` (see
 * ``backend/api/routes/report_configs.py`` and its prefix in
 * ``backend/main.py``). A ``report_configs`` row stores the per-project
 * senior / junior hourly rates (EUR) used by the reporting pipeline to
 * convert AI / human time expenditure into monetary human-cost
 * estimates (DESIGN.md §1.9 Reporting Configuration, §1.23
 * ``ReportConfig``, §6.5 reporting pipeline, business rule R-01). The
 * stored rates back the ``ReportsPage`` / ``ProjectMetricsCard`` /
 * ``AIvsHumanRatioDisplay`` UI (DESIGN.md §3.1, §3.2) and the
 * ``SettingsPage`` rate-override form.
 *
 * Like the other Feat 6 admin pages (``ProfessionalSpecificationPage``,
 * ``RawSpecificationPage``, ``DesignDocumentPage``, …) this surface is
 * deliberately self-contained rather than reaching for a global Zustand
 * store: per DESIGN.md § 3.3 ``reportStore`` backs the end-user
 * ``ReportsPage`` / ``ProjectMetricsCard`` UI, which is a distinct
 * concern from a per-row administrative CRUD editor. When the store
 * grows dedicated admin actions in a later feat this page can switch
 * over without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table filtered by ``project_id`` (the only
 *     filter the backend router exposes — matches the unique-indexed
 *     ``uq_report_configs_project_id`` column). Row-level "View",
 *     "Edit" and "Delete" actions. Results are ordered by
 *     ``created_at DESC`` (newest first) on the backend — matching the
 *     rest of the CRUD surface.
 *   - ``detail`` — read-only view of a single report configuration:
 *     primary key, ``project_id``, both hourly rates and audit
 *     timestamps.
 *   - ``create`` — form that ``POST``s a new report configuration.
 *     ``project_id`` is required; ``senior_hourly_rate_eur`` defaults
 *     to ``75.0000`` and ``junior_hourly_rate_eur`` defaults to
 *     ``35.0000`` (both mirror the DB ``server_default`` and the
 *     Pydantic schema default). The backend enforces
 *     ``UNIQUE(project_id)`` pre-flush so a duplicate-project attempt
 *     surfaces as HTTP 409 (not a raw 500 / ``IntegrityError``).
 *   - ``edit``   — form that ``PATCH``es the mutable rate fields
 *     (``senior_hourly_rate_eur``, ``junior_hourly_rate_eur``).
 *     ``project_id`` is rendered read-only — the row's identity is the
 *     project it configures and must not be rewritten after the fact
 *     (DESIGN.md §1.9 "one config per project").
 *     :class:`ReportConfigUpdate` deliberately omits ``project_id``
 *     (see ``backend/schemas/report_config.py``).
 *
 * ``DELETE`` is a hard delete. ``report_configs`` has **no inbound
 * foreign keys** — no other table references it — so no RESTRICT
 * dependency check is required. The outbound FK ``project_id``
 * (``ON DELETE CASCADE``) keeps the row self-consistent when the
 * parent project is deleted; deleting the configuration itself is the
 * explicit inverse, used to reset the rate model to defaults (a fresh
 * row with the schema / DB defaults can be inserted via ``POST``
 * afterwards). The confirmation dialog warns the user.
 *
 * Rate columns are ``DECIMAL(10, 4)`` on the backend; the wire
 * representation is ``string`` (see ``ReportConfigRead`` /
 * ``ReportConfigCreate`` in ``frontend/src/types/reportConfig.ts``)
 * because JavaScript ``number`` cannot faithfully round-trip arbitrary
 * decimals. Inputs are captured as strings and sanity-checked client-
 * side (non-negative, ≤ 4 fractional digits, ≤ 10 total digits) —
 * malformed values still surface as HTTP 422 from the backend.
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``. The backend maps invalid
 * foreign keys / constraint failures / decimal overflow to HTTP 422,
 * duplicate ``project_id`` to HTTP 409 and missing rows to HTTP 404;
 * all are shown verbatim in the inline error banner.
 *
 * This page sits under ``/admin/report-configs`` alongside the other
 * Feat 6 CRUD surfaces (``/admin/users``, ``/admin/projects``,
 * ``/admin/raw-specifications``,
 * ``/admin/professional-specifications``, …). It is distinct from
 * ``ReportsPage`` (the end-user ``ProjectMetricsCard`` /
 * ``AIvsHumanRatioDisplay`` surface at ``/projects/:slug/reports``,
 * DESIGN.md § 3.1) and ``SettingsPage`` (the rate-override form at
 * ``/settings``, DESIGN.md § 3.1).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  ReportConfigCreate,
  ReportConfigRead,
  ReportConfigUpdate,
} from "../types";

/** REST prefix for the ReportConfig router (see backend/main.py). */
const ENDPOINT = "/report-configs";

/** Page size used by the list view. Matches the backend default (capped at 100). */
const PAGE_SIZE = 20;

/**
 * Canonical server defaults mirrored from the Pydantic schema / DB
 * ``server_default`` so a just-opened create form looks identical to
 * the row the backend would synthesise if the caller omitted the
 * rate fields. Kept as strings to avoid ``number`` round-trip loss.
 */
const DEFAULT_SENIOR_RATE = "75.0000";
const DEFAULT_JUNIOR_RATE = "35.0000";

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
 * ``number`` input ``value`` is always a string, and decimals are
 * transmitted as strings on the wire anyway. UUID inputs enforce the
 * canonical shape via the ``pattern`` attribute; the backend rejects
 * malformed values with HTTP 422.
 */
interface ReportConfigFormState {
  project_id: string;
  senior_hourly_rate_eur: string;
  junior_hourly_rate_eur: string;
}

/** Fresh-form defaults for the create mode — mirror the DB ``server_default``. */
const EMPTY_FORM: ReportConfigFormState = {
  project_id: "",
  senior_hourly_rate_eur: DEFAULT_SENIOR_RATE,
  junior_hourly_rate_eur: DEFAULT_JUNIOR_RATE,
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
 * HTML ``pattern`` expression for a canonical UUID (RFC 4122-style, as
 * emitted by ``uuid.UUID`` on the backend). Rendered on UUID inputs so
 * obvious typos are caught by the browser's constraint-validation API
 * before the form is submitted — the backend would otherwise reject
 * them with a generic 422 after a network round-trip.
 */
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/**
 * HTML ``pattern`` expression for the ``DECIMAL(10, 4)`` rate columns
 * — up to six leading digits before the optional decimal point and up
 * to four fractional digits afterwards. Matches the
 * ``max_digits=10, decimal_places=4`` constraint in
 * ``backend/schemas/report_config.py``. Non-matching values still get
 * rejected server-side with HTTP 422 — this is a front-line ergonomic
 * check only.
 */
const DECIMAL_10_4_PATTERN = "\\d{1,6}(\\.\\d{1,4})?";

/**
 * Format a decimal string (as it comes off the wire) for display.
 *
 * The backend emits ``DECIMAL(10, 4)`` so values are already well-
 * formed strings such as ``"75.0000"``. We render them verbatim — no
 * locale formatting — so the admin surface shows the exact stored
 * value. Falls through for unexpected input shapes so truncated /
 * future rows render without throwing.
 */
function formatRate(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return value;
}

function ReportConfigPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<ReportConfigRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [projectFilter, setProjectFilter] = useState("");

  const [detail, setDetail] = useState<ReportConfigRead | null>(null);
  const [form, setForm] = useState<ReportConfigFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<ReportConfigRead>>(
        ENDPOINT,
        {
          params: {
            skip,
            limit: PAGE_SIZE,
            project_id: projectFilter.trim() || undefined,
          },
        },
      );
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load report configurations.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, projectFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ReportConfigRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to load report configuration.";
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
        const row = await api.get<ReportConfigRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setDetail(row);
        setForm({
          project_id: row.project_id,
          senior_hourly_rate_eur: row.senior_hourly_rate_eur,
          junior_hourly_rate_eur: row.junior_hourly_rate_eur,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Failed to load report configuration.";
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
        "Delete this report configuration? This is a hard delete — no other tables reference report_configs, so no dependency check applies. A fresh row with the schema / DB defaults (senior=75.0000 EUR/h, junior=35.0000 EUR/h) can be inserted via New Report Config afterwards to reset the cost model.",
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
          : "Failed to delete report configuration.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      // Trim rate strings to tolerate trailing/leading whitespace from
      // paste; blank falls back to the schema / DB default so callers
      // that omit the rate field still get the canonical server row.
      const senior = form.senior_hourly_rate_eur.trim();
      const junior = form.junior_hourly_rate_eur.trim();
      const payload: ReportConfigCreate = {
        project_id: form.project_id.trim(),
        senior_hourly_rate_eur: senior.length > 0 ? senior : DEFAULT_SENIOR_RATE,
        junior_hourly_rate_eur: junior.length > 0 ? junior : DEFAULT_JUNIOR_RATE,
      };
      await api.post<ReportConfigRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to create report configuration.";
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
      // ``project_id`` is immutable (see
      // backend/schemas/report_config.py — ReportConfigUpdate
      // deliberately omits it). We only send the mutable rate fields.
      //
      // PATCH semantics on the backend: fields that are ``None`` are
      // treated as "leave unchanged". We send every mutable field
      // unconditionally since the form always has them populated from
      // the seeded row; blank rate input falls back to the seeded
      // value so accidental clears don't overwrite a legitimate rate
      // with a 422.
      const senior = form.senior_hourly_rate_eur.trim();
      const junior = form.junior_hourly_rate_eur.trim();
      const payload: ReportConfigUpdate = {
        senior_hourly_rate_eur:
          senior.length > 0
            ? senior
            : (detail?.senior_hourly_rate_eur ?? DEFAULT_SENIOR_RATE),
        junior_hourly_rate_eur:
          junior.length > 0
            ? junior
            : (detail?.junior_hourly_rate_eur ?? DEFAULT_JUNIOR_RATE),
      };
      await api.patch<ReportConfigRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError
          ? exc.message
          : "Failed to update report configuration.";
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
            Report configurations
          </h2>
          <p className="text-sm text-gray-600">
            Per-project senior / junior hourly rates (EUR) used by the
            reporting pipeline to convert AI / human time expenditure
            into monetary human-cost estimates (DESIGN.md §1.9, §6.5,
            business rule R-01). Exactly one configuration per project
            (``UNIQUE(project_id)``); rates default to ``75.0000`` /
            ``35.0000`` EUR / h. Delete is a hard delete — no other
            tables reference ``report_configs`` — used to reset a
            project's rate model to defaults.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new report configuration"
          >
            New Report Config
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
        <ReportConfigList
          items={items}
          total={total}
          isLoading={isLoading}
          projectFilter={projectFilter}
          onProjectFilterChange={(value) => {
            setSkip(0);
            setProjectFilter(value);
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
        <ReportConfigDetail
          row={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <ReportConfigForm
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

interface ReportConfigListProps {
  items: ReportConfigRead[];
  total: number;
  isLoading: boolean;
  projectFilter: string;
  onProjectFilterChange: (value: string) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function ReportConfigList({
  items,
  total,
  isLoading,
  projectFilter,
  onProjectFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: ReportConfigListProps) {
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
            title="Enter a canonical UUID to restrict to that project's configuration (at most one row), or leave blank to show all configurations."
            placeholder="UUID — blank = all projects"
            className="w-72 rounded-md border border-gray-300 bg-white px-3 py-1.5 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total} configuration{total === 1 ? "" : "s"} total
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
                Config
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Project
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Senior €/h
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Junior €/h
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Created
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Updated
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
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading report configurations…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No report configurations match the current filter.
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
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-900">
                    {formatRate(item.senior_hourly_rate_eur)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-900">
                    {formatRate(item.junior_hourly_rate_eur)}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.created_at)}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.updated_at)}
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

interface ReportConfigDetailProps {
  row: ReportConfigRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function ReportConfigDetail({
  row,
  isLoading,
  onBack,
  onEdit,
}: ReportConfigDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading report configuration…
      </div>
    );
  }
  if (!row) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">
          Report configuration not found.
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
            Configuration ID
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
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Senior hourly rate (EUR)
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatRate(row.senior_hourly_rate_eur)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Junior hourly rate (EUR)
          </dt>
          <dd className="font-mono text-sm text-gray-900">
            {formatRate(row.junior_hourly_rate_eur)}
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

interface ReportConfigFormProps {
  form: ReportConfigFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: ReportConfigFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function ReportConfigForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: ReportConfigFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<ReportConfigFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading report configuration…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit report configuration" : "Create report configuration"}
      </h3>
      <p className="text-sm text-gray-600">
        ``project_id`` is immutable after create — exactly one
        configuration per project (``UNIQUE(project_id)``). Rates are
        stored as ``DECIMAL(10, 4)`` EUR / h with server defaults
        ``75.0000`` (senior) and ``35.0000`` (junior); leave a rate
        blank on create to accept the default, or on edit to retain
        the current value.
      </p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="project_id"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Project ID
            <span className="ml-1 text-xs font-normal text-gray-500">
              (UUID; FK → projects, ON DELETE CASCADE; unique; immutable
              after create)
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
            title="Enter the project UUID this report configuration belongs to. Exactly one configuration per project — duplicates return HTTP 409."
            placeholder="e.g. a31d1a12-…"
            className={`block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm focus:border-primary-500 focus:ring-primary-500 ${
              isEdit ? "bg-gray-100 text-gray-500" : "bg-white text-gray-900"
            }`}
          />
        </div>

        <div>
          <label
            htmlFor="senior_hourly_rate_eur"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Senior hourly rate (EUR)
            <span className="ml-1 text-xs font-normal text-gray-500">
              (DECIMAL(10, 4); default 75.0000; ≥ 0)
            </span>
          </label>
          <input
            id="senior_hourly_rate_eur"
            type="text"
            inputMode="decimal"
            value={form.senior_hourly_rate_eur}
            onChange={(event) =>
              patch({ senior_hourly_rate_eur: event.target.value })
            }
            pattern={DECIMAL_10_4_PATTERN}
            title="Decimal with up to 4 fractional digits (e.g. 75, 75.0000, 125.50). Blank on create uses the schema default 75.0000; blank on edit retains the current value."
            placeholder={DEFAULT_SENIOR_RATE}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="junior_hourly_rate_eur"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Junior hourly rate (EUR)
            <span className="ml-1 text-xs font-normal text-gray-500">
              (DECIMAL(10, 4); default 35.0000; ≥ 0)
            </span>
          </label>
          <input
            id="junior_hourly_rate_eur"
            type="text"
            inputMode="decimal"
            value={form.junior_hourly_rate_eur}
            onChange={(event) =>
              patch({ junior_hourly_rate_eur: event.target.value })
            }
            pattern={DECIMAL_10_4_PATTERN}
            title="Decimal with up to 4 fractional digits (e.g. 35, 35.0000, 42.75). Blank on create uses the schema default 35.0000; blank on edit retains the current value."
            placeholder={DEFAULT_JUNIOR_RATE}
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

export default ReportConfigPage;
