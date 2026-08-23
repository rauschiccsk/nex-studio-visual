import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";

import { getProjectMetricsApi } from "@/services/api/metrics";
import {
  listExternalCosts,
  createExternalCost,
  updateExternalCost,
  deleteExternalCost,
  type ExternalCostRead,
} from "@/services/api/externalCost";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import { useActiveContextStore } from "@/store/activeContextStore";
import { PHASE_LABELS, type BuildPhase } from "@/components/cockpit/labels";
import type { AgentModel } from "@/types/user_agent_setting";
import type { CostRow, CostTotals, ManagerOverhead, ProjectCosts } from "@/types/metrics";

type View = "version" | "cumulative";

// The external + system rows are NOT build phases — their labels live here, not in PHASE_LABELS.
const EXTERNAL_ROW_LABEL = "Externé (ručne zadané)";
const SYSTEM_ROW_LABEL = "Systém (neporovnané)";

// The spec names no source for the model options; the cockpit's own agent models are the only ids that
// occur in practice. Typed as `AgentModel` (aliased from the GENERATED contract) so a backend model roll
// breaks the build instead of drifting silently. An entry carrying any other id still round-trips —
// the select appends the stored value as an extra option (see `modelOptions` below).
const DEFAULT_MODEL: AgentModel = "claude-opus-5";
const MODEL_OPTIONS: { id: AgentModel; label: string }[] = [
  { id: DEFAULT_MODEL, label: "Opus 5" },
  { id: "claude-opus-4-8", label: "Opus 4.8" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6" },
  { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
];

// ─── formatting (honest: null → dash, never a fabricated number) ─────────────

function fmtInt(n: number): string {
  return Math.round(n).toLocaleString("sk-SK");
}

function fmtDuration(seconds: number): string {
  if (seconds <= 0) return "0 s";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  if (m > 0) return `${m} min ${s} s`;
  return `${s} s`;
}

function fmtCost(n: number | null): string {
  return n === null ? "—" : n.toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Money WITH the currency sign — for cards / sublines (the table carries the € in its header). */
function fmtMoney(n: number | null): string {
  return n === null ? "—" : `${fmtCost(n)} €`;
}

function fmtMinutes(min: number | null): string {
  return min === null ? "—" : fmtDuration(min * 60);
}

function fmtPct(n: number): string {
  return `${n.toLocaleString("sk-SK", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}

function fmtCoefficient(n: number): string {
  return n.toLocaleString("sk-SK", { maximumFractionDigits: 1 });
}

/** ISO `YYYY-MM-DD` → `D. M. YYYY` without going through Date (a date-only value has no timezone). */
function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${Number(d)}. ${Number(m)}. ${y}` : iso;
}

function rowLabel(r: CostRow): string {
  if (r.kind === "external") return EXTERNAL_ROW_LABEL;
  if (r.kind === "system") return SYSTEM_ROW_LABEL;
  return PHASE_LABELS[r.key as BuildPhase] ?? "Neznáma fáza";
}

/** `wages` is keyed by ROW key (phases + "externe"), not by build phase alone. */
function wageKeyLabel(key: string): string {
  if (key === "externe") return "Externé";
  return PHASE_LABELS[key as BuildPhase] ?? key;
}

/** A negative amount is a red flag, never a green saving — money is never coloured "good" here. */
function moneyCls(n: number | null): string {
  return n !== null && n < 0 ? "text-[var(--color-status-error)]" : "";
}

function moneyTone(n: number | null): "default" | "error" {
  return n !== null && n < 0 ? "error" : "default";
}

// ─── small presentational pieces ─────────────────────────────────────────────

function Card({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "muted" | "error";
}) {
  const valueCls =
    tone === "error"
      ? "text-[var(--color-status-error)]"
      : tone === "muted"
        ? "text-[var(--color-text-muted)]"
        : "text-[var(--color-text-primary)]";
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4">
      <div className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)]">{label}</div>
      <div className={`text-xl font-bold mt-1 ${valueCls}`}>{value}</div>
      {hint && <div className="text-[11px] text-[var(--color-text-muted)] mt-0.5">{hint}</div>}
    </div>
  );
}

const TD = "px-2 py-1.5 align-top";
const TH = "px-2 py-1.5 text-left font-semibold text-[var(--color-text-muted)] whitespace-nowrap";
const TURNS_HINT = "Počet meraných ťahov (správ) agenta; pri ručne zadaných nákladoch počet záznamov.";
const SHARE_HINT = "Podiel tokenov tohto riadku na všetkých tokenoch v zobrazenom rozsahu.";

function CostRowView({ r }: { r: CostRow }) {
  const isExternal = r.kind === "external";
  const isSystem = r.kind === "system";
  // The system row keeps its info-only italic+muted treatment; the entered row is italic + a "ručne"
  // badge, so a measured and an entered figure never read as the same class of number.
  const rowCls = isSystem
    ? "border-t border-[var(--color-border-default)] italic text-[var(--color-text-muted)]"
    : isExternal
      ? "border-t border-[var(--color-border-default)] italic"
      : "border-t border-[var(--color-border-default)]";
  return (
    <tr className={rowCls}>
      <td className={`${TD} font-medium ${isSystem ? "" : "text-[var(--color-text-secondary)]"}`}>
        {rowLabel(r)}
        {isExternal && (
          <span className="ml-1.5 rounded bg-[var(--color-state-muted-bg)] px-1.5 py-0.5 text-[10px] not-italic text-[var(--color-state-muted-fg)]">
            ručne
          </span>
        )}
      </td>
      <td className={TD}>{fmtInt(r.turns)}</td>
      <td className={`${TD} text-[var(--color-text-muted)]`}>
        {fmtInt(r.input_tokens)} / {fmtInt(r.output_tokens)}
      </td>
      <td className={`${TD} text-[var(--color-text-muted)]`}>{fmtPct(r.share_pct)}</td>
      <td className={`${TD} ${moneyCls(r.agent_cost)}`}>
        {fmtCost(r.agent_cost)}
        {r.agent_cost === null && r.unpriced_model_keys.length > 0 && (
          <div className="text-[10px] text-[var(--color-status-warning)] mt-0.5">
            AI cena zatiaľ nie je k dispozícii
          </div>
        )}
      </td>
      <td className={TD}>{fmtMinutes(r.human_minutes)}</td>
      <td className={`${TD} ${moneyCls(r.human_cost)}`}>{fmtCost(r.human_cost)}</td>
    </tr>
  );
}

/** Scope totals — measured and entered are summed but never merged, so the split is always visible. */
function CostTotalsFoot({ t }: { t: CostTotals }) {
  // Every split row carries the SAME columns as "Spolu" — ťahy and tokeny included. A colSpan here
  // would silently merge metered agent turns with hand-entered records in the "Spolu" row above.
  const splitRow = (
    label: string,
    s: {
      turns: number;
      input_tokens: number;
      output_tokens: number;
      agent_cost: number | null;
      human_minutes: number | null;
      human_cost: number | null;
    },
  ) => (
    <tr className="border-t border-[var(--color-border-default)] text-[var(--color-text-muted)]">
      <td className={TD}>{label}</td>
      <td className={TD}>{fmtInt(s.turns)}</td>
      <td className={TD}>
        {fmtInt(s.input_tokens)} / {fmtInt(s.output_tokens)}
      </td>
      {/* podiel tokenov is a per-ROW figure against the scope total — the halves get no share cell,
          same as "Spolu". */}
      <td className={TD}>—</td>
      <td className={`${TD} ${moneyCls(s.agent_cost)}`}>{fmtCost(s.agent_cost)}</td>
      <td className={TD}>{fmtMinutes(s.human_minutes)}</td>
      <td className={`${TD} ${moneyCls(s.human_cost)}`}>{fmtCost(s.human_cost)}</td>
    </tr>
  );
  return (
    <tfoot>
      <tr className="border-t-2 border-[var(--color-border-default)] bg-[var(--color-surface)] font-semibold">
        <td className={TD}>Spolu</td>
        <td className={TD}>{fmtInt(t.turns)}</td>
        <td className={TD}>
          {fmtInt(t.input_tokens)} / {fmtInt(t.output_tokens)}
        </td>
        <td className={TD}>—</td>
        <td className={`${TD} ${moneyCls(t.agent_cost_total)}`}>{fmtCost(t.agent_cost_total)}</td>
        <td className={TD}>{fmtMinutes(t.human_minutes_total)}</td>
        <td className={`${TD} ${moneyCls(t.human_cost_total)}`}>{fmtCost(t.human_cost_total)}</td>
      </tr>
      {splitRow("z toho namerané", {
        turns: t.turns_measured,
        input_tokens: t.input_tokens_measured,
        output_tokens: t.output_tokens_measured,
        agent_cost: t.agent_cost_measured,
        human_minutes: t.human_minutes_measured,
        human_cost: t.human_cost_measured,
      })}
      {splitRow("z toho ručne zadané", {
        turns: t.turns_external,
        input_tokens: t.input_tokens_external,
        output_tokens: t.output_tokens_external,
        agent_cost: t.agent_cost_external,
        human_minutes: t.human_minutes_external,
        human_cost: t.human_cost_external,
      })}
    </tfoot>
  );
}

function CostTable({
  rows,
  totals,
  manager,
}: {
  rows: CostRow[];
  totals: CostTotals;
  /** When given, the info-only Manažér réžia row is appended below the cost rows. */
  manager?: ManagerOverhead;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-[var(--color-surface)]">
            <th className={TH}>Fáza</th>
            <th className={TH} title={TURNS_HINT}>
              ťahy
            </th>
            <th className={TH}>tokeny vstup/výstup</th>
            <th className={TH} title={SHARE_HINT}>
              podiel tokenov
            </th>
            <th className={TH}>Cena AI (€)</th>
            <th className={TH}>Ľudský čas</th>
            <th className={TH}>Cena ľudskej práce (€)</th>
          </tr>
        </thead>
        <tbody>
          {/* Payload order is a backend contract: phases → externé → systém. */}
          {rows.map((r) => (
            <CostRowView key={`${r.kind}:${r.key}`} r={r} />
          ))}
          {manager && (
            /* Manažér overhead row — measured only (info-only; the v1 priced Director cost is retired) */
            <tr className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
              <td className={`${TD} font-medium text-[var(--color-text-secondary)]`}>Manažér (réžia)</td>
              <td className={`${TD} text-[var(--color-text-muted)]`} colSpan={6}>
                čakanie {fmtDuration(manager.wait_seconds)} · intervencie {manager.interventions}
              </td>
            </tr>
          )}
        </tbody>
        <CostTotalsFoot t={totals} />
      </table>
    </div>
  );
}

// ─── view-scoped slice (a version OR the cumulative project) ─────────────────

interface Scope {
  rows: CostRow[];
  totals: CostTotals;
  manager: ManagerOverhead;
  manager_wait_seconds: number;
  internal_idle_seconds: number | null;
  total_time_seconds: number | null;
}

export default function MetricsPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);

  const [metrics, setMetrics] = useState<ProjectCosts | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<HumanError | null>(null);
  const [view, setView] = useState<View>("version");
  const [selectedVersionId, setSelectedVersionId] = useState<string>("");
  // Bumped after every external-cost mutation — the entry changes the costs, so BOTH the entry list and
  // the metrics payload are re-read.
  const [reloadTick, setReloadTick] = useState(0);

  // ── external cost (manual entry) ──────────────────────────────────────────
  const [entries, setEntries] = useState<ExternalCostRead[]>([]);
  const [entriesError, setEntriesError] = useState<HumanError | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [occurredOn, setOccurredOn] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState<string>(DEFAULT_MODEL);
  const [inputTokens, setInputTokens] = useState("");
  const [outputTokens, setOutputTokens] = useState("");
  const [formVersionId, setFormVersionId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<HumanError | null>(null);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    if (reloadTick === 0) setLoading(true);
    setError(null);
    getProjectMetricsApi(slug)
      .then((m) => {
        if (cancelled) return;
        setMetrics(m);
        const last = m.by_version[m.by_version.length - 1];
        // Keep the Manažér's pick across a reload; fall back to the newest version on first load.
        setSelectedVersionId((cur) => (cur ? cur : last ? last.version_id : ""));
      })
      .catch((err) => {
        if (!cancelled) setError(humanizeApiError(err, "Načítanie nákladov zlyhalo"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, reloadTick]);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    setEntriesError(null);
    listExternalCosts(slug)
      .then((list) => {
        if (!cancelled) setEntries(list);
      })
      .catch((err) => {
        if (!cancelled) setEntriesError(humanizeApiError(err, "Načítanie externých nákladov zlyhalo"));
      });
    return () => {
      cancelled = true;
    };
  }, [slug, reloadTick]);

  const selectedVersion = useMemo(
    () => metrics?.by_version.find((v) => v.version_id === selectedVersionId) ?? null,
    [metrics, selectedVersionId],
  );

  const scope: Scope | null = useMemo(() => {
    if (!metrics) return null;
    if (view === "cumulative") {
      return {
        rows: metrics.rows,
        totals: metrics.totals,
        manager: metrics.manager,
        manager_wait_seconds: metrics.manager.wait_seconds,
        internal_idle_seconds: null, // wall-clock idle is a per-version figure (not summed cumulatively)
        total_time_seconds: null,
      };
    }
    if (!selectedVersion) return null;
    return {
      rows: selectedVersion.rows,
      totals: selectedVersion.totals,
      manager: selectedVersion.manager,
      manager_wait_seconds: selectedVersion.manager_wait_seconds,
      internal_idle_seconds: selectedVersion.internal_idle_seconds,
      total_time_seconds: selectedVersion.total_time_seconds,
    };
  }, [metrics, view, selectedVersion]);

  // An entry with no version counts in the project total and in no version (Part 2).
  function versionLabel(versionId: string | null): string {
    if (!versionId) return "celý projekt";
    return metrics?.by_version.find((v) => v.version_id === versionId)?.version_number ?? "neznáma verzia";
  }

  // A stored id outside MODEL_OPTIONS (a hand-typed / retired model) stays selectable so an edit never
  // silently rewrites it.
  const modelOptions = useMemo(() => {
    const known = MODEL_OPTIONS.map((m) => ({ id: m.id as string, label: m.label }));
    return model && !known.some((m) => m.id === model) ? [...known, { id: model, label: model }] : known;
  }, [model]);

  function resetForm() {
    setEditingId(null);
    setOccurredOn("");
    setDescription("");
    setModel(DEFAULT_MODEL);
    setInputTokens("");
    setOutputTokens("");
    setFormVersionId("");
    setFormError(null);
  }

  function startEdit(e: ExternalCostRead) {
    setEditingId(e.id);
    setOccurredOn(e.occurred_on);
    setDescription(e.description);
    setModel(e.model);
    setInputTokens(String(e.input_tokens));
    setOutputTokens(String(e.output_tokens));
    setFormVersionId(e.version_id === null ? "" : e.version_id);
    setFormError(null);
    setShowForm(true);
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!slug) return;
    setFormError(null);
    setSubmitting(true);
    const payload = {
      occurred_on: occurredOn,
      description: description.trim(),
      model,
      input_tokens: Number(inputTokens),
      output_tokens: Number(outputTokens),
      version_id: formVersionId ? formVersionId : null,
    };
    try {
      if (editingId) {
        await updateExternalCost(slug, editingId, payload);
      } else {
        await createExternalCost(slug, payload);
      }
      resetForm();
      setShowForm(false);
      setReloadTick((t) => t + 1);
    } catch (err) {
      setFormError(humanizeApiError(err, "Uloženie externého nákladu zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(e: ExternalCostRead) {
    if (!slug) return;
    if (!window.confirm(`Odstrániť externý náklad „${e.description}"?`)) return;
    setEntriesError(null);
    try {
      await deleteExternalCost(slug, e.id);
      setReloadTick((t) => t + 1);
    } catch (err) {
      setEntriesError(humanizeApiError(err, `Externý náklad „${e.description}" sa nepodarilo odstrániť`));
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-[var(--color-text-muted)] text-sm gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Načítavam…
      </div>
    );
  }

  if (error || !metrics) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">
          {error ? <ErrorNote error={error} /> : "Náklady nedostupné."}
        </div>
      </div>
    );
  }

  const projectName = selectedProject?.slug === slug ? selectedProject?.name : slug;
  const settingsLink = (
    <button onClick={() => navigate("/settings")} className="text-primary-400 hover:text-primary-300 underline">
      Nastavenia
    </button>
  );
  const wageEntries = Object.entries(metrics.wages);
  const today = new Date().toISOString().slice(0, 10);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Náklady — {projectName}</h1>
        <div className="flex rounded border border-[var(--color-border-default)] overflow-hidden text-[11px]">
          {(["version", "cumulative"] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2.5 py-1 ${
                view === v
                  ? "bg-primary-600 text-white"
                  : "bg-[var(--color-surface)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {v === "version" ? "Verzia" : "Kumulatívne"}
            </button>
          ))}
        </div>
      </div>

      {/* Assumption strip — always visible, never behind a tooltip. */}
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-[11px] text-[var(--color-text-muted)] mb-4">
        {metrics.coefficient_minutes_per_mtok === null ? (
          <>Koeficient nie je nastavený — ľudské stĺpce sa nezobrazujú ({settingsLink}).</>
        ) : (
          <>
            Ľudská práca je prepočet, nie meranie — pri {fmtCoefficient(metrics.coefficient_minutes_per_mtok)} min na
            mil. tokenov.
          </>
        )}{" "}
        · Ceny sú v eurách. ·{" "}
        <span className="font-semibold text-[var(--color-text-secondary)]">
          Cena AI je hodnota spotrebovaného výpočtu v cenníku, nie minutá hotovosť
        </span>{" "}
        — platíme paušál.
        {wageEntries.length > 0 && (
          <div className="mt-1">
            Hodinové sadzby:{" "}
            {wageEntries.map(([k, v], i) => (
              <span key={k}>
                {i > 0 && " · "}
                {wageKeyLabel(k)} {fmtMoney(v)}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Unset-config banner (per dimension) — WHY a column is all dashes; the coefficient case is the
          strip's job above. Deliberately carries no second "Nastavenia" link: one settings entry point
          per screen (the strip's), no duplicated navigation. */}
      {(!metrics.pricing_configured || !metrics.wages_configured) && (
        <div className="rounded-lg border border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] px-3 py-2 text-xs text-[var(--color-state-warning-fg)] mb-4">
          {!metrics.pricing_configured && <>Ceny modelov nenastavené. </>}
          {!metrics.wages_configured && <>Mzdy nenastavené. </>}
          Chýbajúce stĺpce sa zobrazia až po doplnení v Nastaveniach → Náklady (koeficient, mzdy, ceny).
        </div>
      )}

      {/* Headline cost cards */}
      {scope && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
          <Card
            label="Cena AI"
            value={fmtMoney(scope.totals.agent_cost_total)}
            hint={`z toho ručne zadané: ${fmtMoney(scope.totals.agent_cost_external)}`}
            tone={moneyTone(scope.totals.agent_cost_total)}
          />
          <Card
            label="Cena ľudskej práce"
            value={fmtMoney(scope.totals.human_cost_total)}
            hint={`z toho ručne zadané: ${fmtMoney(scope.totals.human_cost_external)}`}
            tone={moneyTone(scope.totals.human_cost_total)}
          />
        </div>
      )}

      {/* Version selector */}
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">
          {view === "cumulative" ? "Podľa fázy — kumulatívne" : `Podľa fázy — ${selectedVersion?.version_number ?? ""}`}
        </h2>
        <select
          value={selectedVersionId}
          onChange={(e) => setSelectedVersionId(e.target.value)}
          disabled={view === "cumulative"}
          className="bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded px-2 py-1 text-xs text-[var(--color-text-primary)] disabled:opacity-40"
        >
          {metrics.by_version.map((v) => (
            <option key={v.version_id} value={v.version_id}>
              {v.version_number}
            </option>
          ))}
        </select>
      </div>

      {/* Per-phase cost table (active scope) */}
      {scope && (
        <div className="mb-4">
          <CostTable rows={scope.rows} totals={scope.totals} manager={scope.manager} />
        </div>
      )}

      {/* Idle panel — separate, NEVER mixed into AI time */}
      {scope && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-6">
          <Card
            label="Čakanie na Manažéra (prestoj A)"
            value={fmtDuration(scope.manager_wait_seconds)}
            hint="réžia zapojenia človeka, cieľ → 0"
            tone="muted"
          />
          <Card
            label="Interná nečinnosť (prestoj B)"
            value={scope.internal_idle_seconds === null ? "—" : fmtDuration(scope.internal_idle_seconds)}
            hint={scope.internal_idle_seconds === null ? "rozpätie neznáme" : "reálny čas medzi ťahmi"}
            tone="muted"
          />
          <Card
            label="Čas začiatok → koniec"
            value={scope.total_time_seconds === null ? "—" : fmtDuration(scope.total_time_seconds)}
            hint={view === "cumulative" ? "pre verziu (vyber verziu)" : "reálny čas verzie"}
            tone="muted"
          />
        </div>
      )}

      {/* Per-version breakdown — the same table shape, one per version */}
      <h2 className="text-sm font-semibold text-[var(--color-text-secondary)] mb-2">Podľa verzie</h2>
      {metrics.by_version.length === 0 ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4 text-sm text-[var(--color-text-muted)] mb-6">
          Zatiaľ žiadne namerané dáta — náklady sa naplnia po prvom zostavení.
        </div>
      ) : (
        <div className="space-y-4 mb-6">
          {metrics.by_version.map((v) => (
            <div key={v.version_id}>
              <div className="font-mono text-xs text-[var(--color-text-secondary)] mb-1">{v.version_number}</div>
              <CostTable rows={v.rows} totals={v.totals} />
            </div>
          ))}
        </div>
      )}

      {/* Externé náklady — manual entry (work the cockpit cannot meter) */}
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Externé náklady</h2>
        <button
          onClick={() => {
            resetForm();
            setShowForm((v) => !v);
          }}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          <Plus className="h-3.5 w-3.5" /> Pridať náklad
        </button>
      </div>
      <p className="text-xs text-[var(--color-text-muted)] mb-3">
        Práca urobená mimo kokpitu (terminál, vývojár priamo). Oceňuje sa rovnakými cenami modelov a rovnakým
        koeficientom ako meraná práca, ale vždy zostáva samostatný riadok — ručne zadané čísla sa nikdy nezlúčia s
        nameranými.
      </p>

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-4 space-y-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4"
        >
          <div className="text-xs font-semibold text-[var(--color-text-secondary)]">
            {editingId ? "Upraviť externý náklad" : "Nový externý náklad"}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Dátum *</span>
              <input
                type="date"
                value={occurredOn}
                onChange={(e) => setOccurredOn(e.target.value)}
                required
                max={today}
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs md:col-span-2">
              <span className="text-[var(--color-text-secondary)]">Popis *</span>
              <input
                spellCheck={false}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                required
                maxLength={500}
                placeholder="Doladenie migrácie v termináli"
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Model *</span>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              >
                {modelOptions.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Tokeny vstup *</span>
              <input
                type="number"
                min={0}
                step={1}
                value={inputTokens}
                onChange={(e) => setInputTokens(e.target.value)}
                required
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Tokeny výstup *</span>
              <input
                type="number"
                min={0}
                step={1}
                value={outputTokens}
                onChange={(e) => setOutputTokens(e.target.value)}
                required
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs md:col-span-3">
              <span className="text-[var(--color-text-secondary)]">Verzia</span>
              <select
                value={formVersionId}
                onChange={(e) => setFormVersionId(e.target.value)}
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              >
                <option value="">celý projekt</option>
                {metrics.by_version.map((v) => (
                  <option key={v.version_id} value={v.version_id}>
                    {v.version_number}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <ErrorNote error={formError} />

          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={submitting || !occurredOn || !description.trim() || !inputTokens || !outputTokens}
              className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Uložiť
            </button>
            <button
              type="button"
              onClick={() => {
                resetForm();
                setShowForm(false);
              }}
              className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            >
              Zrušiť
            </button>
          </div>
        </form>
      )}

      <ErrorNote error={entriesError} className="mb-2" />

      {entries.length === 0 ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4 text-sm text-[var(--color-text-muted)]">
          Zatiaľ žiadne externé náklady. Pridaj prvý cez „Pridať náklad“.
        </div>
      ) : (
        <div className="divide-y divide-[var(--color-border-default)] overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)]">
          {entries.map((e) => (
            <div key={e.id} className="flex items-center justify-between gap-3 p-3 text-xs">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[var(--color-text-muted)]">{fmtDate(e.occurred_on)}</span>
                  <span className="font-medium text-[var(--color-text-primary)]">{e.description}</span>
                  <span className="rounded bg-[var(--color-state-muted-bg)] px-1.5 py-0.5 text-[10px] text-[var(--color-state-muted-fg)]">
                    ručne
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
                  {e.model} · {fmtInt(e.input_tokens)} / {fmtInt(e.output_tokens)} tokenov ·{" "}
                  {versionLabel(e.version_id)}
                </div>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                <button
                  onClick={() => startEdit(e)}
                  title="Upraviť externý náklad"
                  className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-primary)]"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  onClick={() => handleDelete(e)}
                  title="Odstrániť externý náklad"
                  className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-state-error-bg)] hover:text-[var(--color-state-error-fg)]"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
