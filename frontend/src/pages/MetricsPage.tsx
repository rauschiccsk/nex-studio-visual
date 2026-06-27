import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

import { getProjectMetricsApi } from "@/services/api/metrics";
import { useTheme } from "@/contexts/ThemeContext";
import { useActiveContextStore } from "@/store/activeContextStore";
import { PHASE_LABELS, type BuildPhase } from "@/components/cockpit/labels";
import type {
  ManagerOverhead,
  ProjectMetrics,
  RoiHeadline,
  PhaseMetric,
  SystemOverheadRow,
  VersionMetrics,
} from "@/types/metrics";

type View = "version" | "cumulative";

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

function fmtRatio(n: number | null): string {
  return n === null ? "—" : `${n.toLocaleString("sk-SK", { maximumFractionDigits: 1 })}×`;
}

function fmtMinutes(min: number | null): string {
  return min === null ? "—" : fmtDuration(min * 60);
}

function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase as BuildPhase] ?? phase;
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
  tone?: "default" | "good" | "muted";
}) {
  const valueCls =
    tone === "good"
      ? "text-[var(--color-status-success)]"
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

function PhaseRow({ r }: { r: PhaseMetric }) {
  const valuePair =
    r.agent_cost === null
      ? "— / —"
      : `${fmtCost(r.agent_value_in)} / ${fmtCost(r.agent_value_out)}`;
  return (
    <tr className="border-t border-[var(--color-border-default)]">
      <td className={`${TD} font-medium text-[var(--color-text-secondary)]`}>{phaseLabel(r.phase)}</td>
      <td className={TD}>{fmtDuration(r.active_seconds)}</td>
      <td className={`${TD} text-[var(--color-text-muted)]`}>{fmtInt(r.input_tokens)} / {fmtInt(r.output_tokens)}</td>
      <td className={`${TD} text-[var(--color-text-muted)]`}>{r.parse_attempts > 0 ? r.parse_attempts : "—"}</td>
      <td className={TD}>
        {valuePair}
        {r.agent_cost === null && r.unpriced_model_keys.length > 0 && (
          <div className="text-[10px] text-[var(--color-status-warning)] mt-0.5">
            AI cena chýba: model {r.unpriced_model_keys.join(", ")}
          </div>
        )}
      </td>
      <td className={TD}>{fmtMinutes(r.human_minutes)}</td>
      <td className={TD}>{fmtCost(r.human_cost)}</td>
      <td className={TD}>{fmtRatio(r.x_faster)}</td>
      <td className={TD}>{fmtRatio(r.m_cheaper)}</td>
      <td className={`${TD} ${r.eur_saved !== null ? "text-[var(--color-status-success)]" : ""}`}>{fmtCost(r.eur_saved)}</td>
    </tr>
  );
}

// ─── view-scoped slice (a version OR the cumulative project) ─────────────────

interface Scope {
  by_phase: PhaseMetric[];
  system_overhead: SystemOverheadRow;
  manager: ManagerOverhead;
  roi: RoiHeadline;
  manager_wait_seconds: number;
  internal_idle_seconds: number | null;
  total_time_seconds: number | null;
}

export default function MetricsPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);

  const gridStroke = isDark ? "#334155" : "#e2e8f0";
  const tickFill = isDark ? "#94a3b8" : "#64748b";
  const tooltipBg = isDark ? "#1e293b" : "#ffffff";
  const tooltipBorder = isDark ? "#334155" : "#e2e8f0";
  const tooltipColor = isDark ? "#f1f5f9" : "#0f172a";

  const [metrics, setMetrics] = useState<ProjectMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>("version");
  const [selectedVersionId, setSelectedVersionId] = useState<string>("");

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    setLoading(true);
    getProjectMetricsApi(slug)
      .then((m) => {
        if (cancelled) return;
        setMetrics(m);
        const last = m.by_version[m.by_version.length - 1];
        if (last) setSelectedVersionId(last.version_id);
      })
      .catch(() => {
        if (!cancelled) setError("Nepodarilo sa načítať metriky.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const selectedVersion: VersionMetrics | null = useMemo(
    () => metrics?.by_version.find((v) => v.version_id === selectedVersionId) ?? null,
    [metrics, selectedVersionId],
  );

  const scope: Scope | null = useMemo(() => {
    if (!metrics) return null;
    if (view === "cumulative") {
      return {
        by_phase: metrics.by_phase,
        system_overhead: metrics.system_overhead,
        manager: metrics.manager,
        roi: metrics.roi,
        manager_wait_seconds: metrics.manager.wait_seconds,
        internal_idle_seconds: null, // wall-clock idle is a per-version figure (not summed cumulatively)
        total_time_seconds: null,
      };
    }
    if (!selectedVersion) return null;
    return {
      by_phase: selectedVersion.by_phase,
      system_overhead: selectedVersion.system_overhead,
      manager: selectedVersion.manager,
      roi: selectedVersion.roi,
      manager_wait_seconds: selectedVersion.manager_wait_seconds,
      internal_idle_seconds: selectedVersion.internal_idle_seconds,
      total_time_seconds: selectedVersion.total_time_seconds,
    };
  }, [metrics, view, selectedVersion]);

  const phaseMinChartData = useMemo(() => {
    if (!scope) return [];
    return scope.by_phase.map((r) => ({
      name: phaseLabel(r.phase),
      "AI (min)": Math.round((r.active_seconds / 60) * 10) / 10,
      "človek (min)": r.human_minutes === null ? 0 : Math.round(r.human_minutes * 10) / 10,
    }));
  }, [scope]);

  const phaseCostChartData = useMemo(() => {
    if (!scope) return [];
    return scope.by_phase.map((r) => ({
      name: phaseLabel(r.phase),
      "AI (€)": r.agent_cost ?? 0,
      "človek (€)": r.human_cost ?? 0,
    }));
  }, [scope]);

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
          {error || "Metriky nedostupné."}
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
  const roi = scope?.roi ?? metrics.roi;
  const perBuildSaved = selectedVersion?.roi.eur_saved ?? null;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Metriky &amp; ROI — podľa fázy</h1>
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
      <p className="text-xs text-[var(--color-text-muted)] mb-4">
        Nameraná práca agenta vs. ekvivalentný ľudský čas pre{" "}
        <span className="text-[var(--color-text-secondary)]">{projectName}</span> — z tej istej bázy (všetky tokeny per
        fáza). Čísla, ktoré chýbajú (ceny / kurzy / mzdy), sa nezobrazujú vymyslené.
      </p>

      {/* Headline ROI */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <Card
          label="Rýchlejšie ako človek"
          value={fmtRatio(roi.x_faster)}
          hint={roi.x_faster === null ? "Kurzy nenastavené" : "ľudský čas (z tokenov) vs. aktívny AI čas"}
          tone={roi.x_faster !== null ? "good" : "muted"}
        />
        <Card
          label="Lacnejšie ako človek"
          value={fmtRatio(roi.m_cheaper)}
          hint={roi.m_cheaper === null ? "Ceny / kurzy / mzdy nenastavené" : "ľudská cena vs. API cena (hodnota compute)"}
          tone={roi.m_cheaper !== null ? "good" : "muted"}
        />
        <Card
          label="Ušetrené € (tento build)"
          value={fmtCost(perBuildSaved)}
          hint={selectedVersion ? selectedVersion.version_number : undefined}
          tone={perBuildSaved !== null ? "good" : "muted"}
        />
        <Card
          label="Ušetrené € (kumulatívne)"
          value={fmtCost(metrics.roi.eur_saved)}
          hint={`za ${metrics.roi.covered_versions} z ${metrics.roi.total_versions} verzií`}
          tone={metrics.roi.eur_saved !== null ? "good" : "muted"}
        />
      </div>

      {/* ROI-angle + model-drift badges */}
      <div className="flex flex-wrap gap-2 mb-3 text-[11px]">
        <span className="rounded border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-2 py-1 text-[var(--color-text-muted)]">
          Platíme flat Claude MAX → marginálny náklad ~0; vyššie je trhová hodnota spotrebovaného compute.
        </span>
        {roi.unknown_model_token_pct > 0 && (
          <span className="rounded border border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] px-2 py-1 text-[var(--color-state-warning-fg)]">
            {roi.unknown_model_token_pct.toLocaleString("sk-SK", { maximumFractionDigits: 1 })} % tokenov bez
            rozpoznaného modelu — cena flat.
          </span>
        )}
      </div>

      {/* Unset-config banner (per dimension) */}
      {!roi.configured && (
        <div className="rounded-lg border border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] px-3 py-2 text-xs text-[var(--color-state-warning-fg)] mb-4">
          {!roi.pricing_configured && <>Ceny modelov nenastavené. </>}
          {!roi.rates_configured && <>Kurzy (tokeny→minúty) nenastavené. </>}
          {!roi.wages_configured && <>Mzdy nenastavené. </>}
          Ľudská / AI strana sa zobrazí až po doplnení v {settingsLink} → Metriky / ROI.
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

      {/* Per-role table */}
      {scope && (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] overflow-x-auto mb-4">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-[var(--color-surface)]">
                <th className={TH}>Fáza</th>
                <th className={TH}>AI čas</th>
                <th className={TH}>tokeny IN/OUT</th>
                <th className={TH}>rework</th>
                <th className={TH}>hodnota IN/OUT (€)</th>
                <th className={TH}>človek čas</th>
                <th className={TH}>človek (€)</th>
                <th className={TH}>N×</th>
                <th className={TH}>M×</th>
                <th className={TH}>ušetrené €</th>
              </tr>
            </thead>
            <tbody>
              {scope.by_phase.map((r) => (
                <PhaseRow key={r.phase} r={r} />
              ))}
              {/* Manažér overhead row — measured only (info-only; the v1 priced Director cost is retired) */}
              <tr className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
                <td className={`${TD} font-medium text-[var(--color-text-secondary)]`}>
                  Manažér (overhead)
                </td>
                <td className={`${TD} text-[var(--color-text-muted)]`} colSpan={9}>
                  čakanie {fmtDuration(scope.manager.wait_seconds)} · intervencie {scope.manager.interventions}
                </td>
              </tr>
              {/* System / engine row — info-only, foots the table */}
              <tr className="border-t border-[var(--color-border-default)] italic text-[var(--color-text-muted)]">
                <td className={TD}>Systém / engine (neporovnané)</td>
                <td className={TD}>{fmtDuration(scope.system_overhead.active_seconds)}</td>
                <td className={TD}>
                  {fmtInt(scope.system_overhead.input_tokens)} / {fmtInt(scope.system_overhead.output_tokens)}
                </td>
                <td className={TD}>—</td>
                <td className={TD}>{fmtCost(scope.system_overhead.agent_cost)}</td>
                <td className={TD}>—</td>
                <td className={TD}>—</td>
                <td className={TD}>—</td>
                <td className={TD}>—</td>
                <td className={TD}>—</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Idle panel — separate, NEVER mixed into AI time */}
      {scope && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-6">
          <Card
            label="Čakanie na Manažéra (prestoj A)"
            value={fmtDuration(scope.manager_wait_seconds)}
            hint="human-in-the-loop overhead, cieľ → 0"
            tone="muted"
          />
          <Card
            label="Interný idle (prestoj B)"
            value={scope.internal_idle_seconds === null ? "—" : fmtDuration(scope.internal_idle_seconds)}
            hint={scope.internal_idle_seconds === null ? "rozpätie neznáme" : "reálny wall-clock medzi ťahmi"}
            tone="muted"
          />
          <Card
            label="Čas start → koniec"
            value={scope.total_time_seconds === null ? "—" : fmtDuration(scope.total_time_seconds)}
            hint={view === "cumulative" ? "per verzia (vyber verziu)" : "wall-clock verzie"}
            tone="muted"
          />
        </div>
      )}

      {/* Per-version breakdown */}
      <h2 className="text-sm font-semibold text-[var(--color-text-secondary)] mb-2">Podľa verzie</h2>
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] divide-y divide-[var(--color-border-default)] mb-6">
        {metrics.by_version.length === 0 ? (
          <div className="p-4 text-sm text-[var(--color-text-muted)]">
            Žiadne pipeline dáta — metriky sa naplnia po prvom builde.
          </div>
        ) : (
          metrics.by_version.map((v) => (
            <div key={v.version_id} className="p-3 grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
              <div className="font-mono text-[var(--color-text-secondary)]">{v.version_number}</div>
              <div className="text-[var(--color-text-secondary)]">
                {fmtInt(v.usage.input_tokens + v.usage.output_tokens)} tokenov
              </div>
              <div className="text-[var(--color-text-secondary)]">{fmtDuration(v.usage.duration_seconds)} AI</div>
              <div className="text-[var(--color-text-muted)]">{fmtRatio(v.roi.m_cheaper)} lacnejšie</div>
              <div className={v.roi.eur_saved !== null ? "text-[var(--color-status-success)]" : "text-[var(--color-text-muted)]"}>
                {v.roi.eur_saved !== null ? `${fmtCost(v.roi.eur_saved)} € ušetrené` : "ROI —"}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Per-role charts (active scope) */}
      {scope && (
        <>
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-3 mb-4">
            <div className="text-[11px] text-[var(--color-text-muted)] mb-2">Čas podľa fázy — AI vs. človek (min)</div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={phaseMinChartData} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                <XAxis dataKey="name" tick={{ fontSize: 10, fill: tickFill }} />
                <YAxis tick={{ fontSize: 10, fill: tickFill }} />
                <Tooltip contentStyle={{ background: tooltipBg, border: `1px solid ${tooltipBorder}`, color: tooltipColor, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="AI (min)" fill="#38bdf8" />
                <Bar dataKey="človek (min)" fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-3 mb-4">
            <div className="text-[11px] text-[var(--color-text-muted)] mb-2">Cena podľa fázy — AI vs. človek (€)</div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={phaseCostChartData} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                <XAxis dataKey="name" tick={{ fontSize: 10, fill: tickFill }} />
                <YAxis tick={{ fontSize: 10, fill: tickFill }} />
                <Tooltip contentStyle={{ background: tooltipBg, border: `1px solid ${tooltipBorder}`, color: tooltipColor, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="AI (€)" fill="#818cf8" />
                <Bar dataKey="človek (€)" fill="#34d399" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}
