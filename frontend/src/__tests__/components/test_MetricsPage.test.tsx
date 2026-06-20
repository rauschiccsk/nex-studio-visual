/**
 * E5 (metrics redesign) — MetricsPage renders the role-based ROI shape and is HONEST: unset
 * pricing/rates/wages show "nenastavené" with a Settings link, never a fabricated number.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type {
  DirectorMetric,
  ProjectMetrics,
  RoiHeadline,
  RoleMetric,
  SystemOverheadRow,
} from "@/types/metrics";

const { mockGetMetrics } = vi.hoisted(() => ({ mockGetMetrics: vi.fn() }));

vi.mock("@/services/api/metrics", () => ({ getProjectMetricsApi: mockGetMetrics }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useParams: () => ({ slug: "p1" }), useNavigate: () => vi.fn() };
});

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "p1", name: "Projekt 1" } }),
}));

// Recharts needs real layout dimensions (jsdom has none) — stub to plain wrappers.
vi.mock("recharts", () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  const Empty = () => null;
  return {
    ResponsiveContainer: Passthrough,
    BarChart: Passthrough,
    Bar: Empty,
    XAxis: Empty,
    YAxis: Empty,
    Tooltip: Empty,
    Legend: Empty,
    CartesianGrid: Empty,
  };
});

import MetricsPage from "@/pages/MetricsPage";
import { ThemeProvider } from "@/contexts/ThemeContext";

const usage = { input_tokens: 1000, output_tokens: 500, duration_seconds: 600, messages: 3 };

function role(r: string, over: Partial<RoleMetric> = {}): RoleMetric {
  return {
    role: r,
    active_seconds: 600,
    internal_idle_seconds: null,
    input_tokens: 1000,
    output_tokens: 500,
    parse_attempts: 1,
    agent_cost: null,
    agent_value_in: null,
    agent_value_out: null,
    by_model: {},
    unpriced_model_keys: [],
    human_minutes: null,
    human_cost: null,
    x_faster: null,
    m_cheaper: null,
    eur_saved: null,
    ...over,
  };
}

const sysOverhead: SystemOverheadRow = { input_tokens: 0, output_tokens: 0, active_seconds: 0, agent_cost: null };
const director: DirectorMetric = {
  interventions: 2,
  agent_wait_seconds: 120,
  agent_director_cost: null,
  human_director_minutes: null,
  human_director_cost: null,
};

const ROLES = ["coordinator", "designer", "customer", "implementer", "auditor"];

const UNSET_ROI: RoiHeadline = {
  agent_active_minutes: 10,
  human_minutes_total: null,
  agent_cost_total: null,
  human_cost_total: null,
  x_faster: null,
  m_cheaper: null,
  eur_saved: null,
  unknown_model_token_pct: 0,
  flat_subscription: true,
  marginal_cost_eur: 0,
  configured: false,
  pricing_configured: false,
  rates_configured: false,
  wages_configured: false,
  covered_versions: 0,
  total_versions: 1,
};

const CONFIGURED_ROI: RoiHeadline = {
  ...UNSET_ROI,
  human_minutes_total: 120,
  agent_cost_total: 0.0285,
  human_cost_total: 120,
  x_faster: 240,
  m_cheaper: 97.7,
  eur_saved: 119.97,
  configured: true,
  pricing_configured: true,
  rates_configured: true,
  wages_configured: true,
  covered_versions: 1,
};

function project(roi: RoiHeadline, byRole: RoleMetric[]): ProjectMetrics {
  return {
    project_id: "pid",
    slug: "p1",
    usage,
    by_role: byRole,
    system_overhead: sysOverhead,
    director,
    by_version: [
      {
        version_id: "v1",
        version_number: "1.0.0",
        status: "active",
        usage,
        by_role: byRole,
        system_overhead: sysOverhead,
        director,
        director_wait_seconds: 120,
        internal_idle_seconds: null,
        total_time_seconds: null,
        roi,
      },
    ],
    roi,
  };
}

const UNSET = project(UNSET_ROI, ROLES.map((r) => role(r)));
const CONFIGURED = project(
  CONFIGURED_ROI,
  ROLES.map((r) => role(r, { agent_cost: 0.0105, human_minutes: 60, human_cost: 60, x_faster: 240, m_cheaper: 97.7, eur_saved: 59.9 })),
);

describe("MetricsPage (metrics redesign)", () => {
  beforeEach(() => mockGetMetrics.mockReset());

  it("renders the headline ROI when configured", async () => {
    mockGetMetrics.mockResolvedValue(CONFIGURED);
    render(
      <ThemeProvider username="test">
        <MetricsPage />
      </ThemeProvider>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: /Metriky/i })).toBeInTheDocument());
    // 240× appears in the headline card AND the per-role table — at least one is enough
    expect(screen.getAllByText(/240×/).length).toBeGreaterThan(0);
    // the per-dimension unset banner is absent when everything is configured
    expect(screen.queryByText(/Mzdy nenastavené/i)).toBeNull();
  });

  it("shows 'nenastavené' (never a fake number) when pricing + rates + wages are unset", async () => {
    mockGetMetrics.mockResolvedValue(UNSET);
    render(
      <ThemeProvider username="test">
        <MetricsPage />
      </ThemeProvider>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: /Metriky/i })).toBeInTheDocument());
    // the unset banner + a Settings link, not a fabricated cost
    expect(screen.getAllByText(/nenastavené/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Nastavenia/i })).toBeInTheDocument();
    // Director-wait is still shown (it's measured, not priced)
    expect(screen.getByText(/Čakanie na Directora/i)).toBeInTheDocument();
  });
});
