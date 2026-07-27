// Per-scope COST rows for the Náklady screen (CR-V2-063; replaces the v2 ROI shape, CR-V2-029).
// Mirrors backend/schemas/metrics.py (ProjectCostsRead) — hand-written on purpose: the page does NOT
// read the generated contract. Honest by construction: any figure depending on an unset price /
// coefficient / wage is null and renders "—", never a fabricated 0. Measured and hand-entered figures
// are summed but never merged — every total carries its `…_external` split.

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  duration_seconds: number;
  messages: number;
}

// `phase` = a build phase (measured), `external` = the manually entered row, `system` = un-phased
// engine tokens (agent-only: no human equivalent exists, so its human figures are always null).
export type CostRowKind = "phase" | "external" | "system";

export interface CostRow {
  key: string; // phase key (COMPARISON_PHASES), or "externe", or "system"
  kind: CostRowKind;
  turns: number; // metered messages (external rows: number of entries)
  input_tokens: number;
  output_tokens: number;
  // this row's tokens ÷ the scope's total tokens × 100 — always computable (never null), which is why
  // it is a TOKEN share and not a cost share (cost is null whenever a model is unpriced).
  share_pct: number;
  agent_cost: number | null; // tokens × per-model price; null if any present model is unpriced
  unpriced_model_keys: string[]; // drives the per-row "AI cena zatiaľ nie je k dispozícii" note
  human_minutes: number | null; // always null for kind="system"
  human_cost: number | null; // always null for kind="system"
  active_seconds: number; // real measured compute time (0 for external rows)
}

export interface CostTotals {
  // Counted figures carry the same measured/entered split as the money ones — a metered agent turn is
  // never merged with a hand-entered record (the footer renders both halves).
  turns: number;
  turns_measured: number; // metered agent messages (phases + system)
  turns_external: number; // number of hand-entered records, NOT agent messages
  input_tokens: number;
  input_tokens_measured: number;
  input_tokens_external: number;
  output_tokens: number;
  output_tokens_measured: number;
  output_tokens_external: number;
  agent_cost_measured: number | null;
  agent_cost_external: number | null; // a real 0 when nothing was entered — never null for that reason
  agent_cost_total: number | null; // null when EITHER half is null
  human_minutes_measured: number | null;
  human_minutes_external: number | null;
  human_minutes_total: number | null;
  human_cost_measured: number | null;
  human_cost_external: number | null;
  human_cost_total: number | null;
}

export interface ManagerOverhead {
  // Manažér (human-in-the-loop) overhead — measured wait + intervention count, info-only.
  interventions: number;
  wait_seconds: number; // measured (idle-a)
}

export interface VersionCosts {
  version_id: string;
  version_number: string;
  // Shipped by the backend, never read by the screen (the per-version section renders `rows`/`totals`
  // now) → declared optional so the mirror only OBLIGES what the screen actually consumes.
  status?: string;
  usage?: UsageTotals;
  rows: CostRow[];
  totals: CostTotals;
  manager: ManagerOverhead;
  manager_wait_seconds: number;
  internal_idle_seconds: number | null;
  total_time_seconds: number | null;
}

export interface ProjectCosts {
  // Identity fields — shipped by the backend, not consumed by the screen (see VersionCosts above).
  project_id?: string;
  slug?: string;
  usage?: UsageTotals;
  rows: CostRow[]; // cumulative, in payload order: phases → external → system
  totals: CostTotals;
  by_version: VersionCosts[];
  manager: ManagerOverhead;
  // ── assumption block (the screen must display it) ──────────────────────────
  coefficient_minutes_per_mtok: number | null; // the single tokens→minutes setting; null when unset
  wages: Record<string, number | null>; // row key → hourly wage; null when unset ("system" never present)
  currency: string;
  pricing_configured: boolean;
  coefficient_configured: boolean;
  wages_configured: boolean;
}
