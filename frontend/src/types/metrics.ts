// Role-based project metrics / ROI (E5; metrics redesign). Mirrors backend/schemas/metrics.py
// (ProjectMetricsRead). Honest by construction: any figure depending on an unset price/rate/wage is
// null, never fabricated; a ratio is null whenever EITHER side is null.

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  duration_seconds: number;
  messages: number;
}

export interface ModelTokens {
  input_tokens: number;
  output_tokens: number;
}

export interface RoleMetric {
  role: string; // one of COMPARISON_ROLES
  // AGENT (measured)
  active_seconds: number;
  internal_idle_seconds: number | null; // inter-turn idle is not role-attributable → always null
  input_tokens: number;
  output_tokens: number;
  parse_attempts: number; // rework evidence
  agent_cost: number | null; // tokens × per-model API price; null if any present model unpriced
  agent_value_in: number | null;
  agent_value_out: number | null;
  by_model: Record<string, ModelTokens>;
  unpriced_model_keys: string[]; // drives the per-row "AI cena chýba: model X" badge
  // HUMAN (token-derived)
  human_minutes: number | null; // tokens × per-role rate
  human_cost: number | null; // human_minutes × per-role wage
  // ratios — null when EITHER side is null
  x_faster: number | null;
  m_cheaper: number | null;
  eur_saved: number | null;
}

export interface SystemOverheadRow {
  // un-compared engine tokens; info-only; foots the per-role table
  input_tokens: number;
  output_tokens: number;
  active_seconds: number;
  agent_cost: number | null;
}

export interface DirectorMetric {
  interventions: number;
  agent_wait_seconds: number; // measured (idle-a)
  agent_director_cost: number | null; // wait × director wage (empirical, agent side)
  human_director_minutes: number | null;
  human_director_cost: number | null; // human-side, same rate model
}

export interface RoiHeadline {
  agent_active_minutes: number;
  human_minutes_total: number | null;
  agent_cost_total: number | null; // Σ role agent cost + director (NOT incl. system)
  human_cost_total: number | null;
  x_faster: number | null; // human-time vs agent ACTIVE time
  m_cheaper: number | null;
  eur_saved: number | null;
  unknown_model_token_pct: number; // model-drift visibility
  flat_subscription: boolean;
  marginal_cost_eur: number;
  configured: boolean; // pricing AND rates AND wages
  pricing_configured: boolean;
  rates_configured: boolean;
  wages_configured: boolean;
  covered_versions: number; // cumulative coverage
  total_versions: number;
}

export interface VersionMetrics {
  version_id: string;
  version_number: string;
  status: string;
  usage: UsageTotals;
  by_role: RoleMetric[]; // 5 agent roles
  system_overhead: SystemOverheadRow;
  director: DirectorMetric;
  director_wait_seconds: number;
  internal_idle_seconds: number | null;
  total_time_seconds: number | null;
  roi: RoiHeadline;
}

export interface ProjectMetrics {
  project_id: string;
  slug: string;
  usage: UsageTotals; // cumulative grand total
  by_role: RoleMetric[]; // cumulative per role
  system_overhead: SystemOverheadRow;
  director: DirectorMetric;
  by_version: VersionMetrics[];
  roi: RoiHeadline; // cumulative
}
