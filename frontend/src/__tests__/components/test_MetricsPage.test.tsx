/**
 * MetricsPage — the Náklady screen (CR-V2-063, spec cases 11–14; replaces the v2 ROI coverage of
 * CR-V2-029).
 *
 * The screen is HONEST by construction: a `null` figure renders "—" and is never fabricated as 0;
 * measured and hand-entered figures are summed but NEVER merged (the entered row carries a `ručne`
 * badge and its own footer split); the assumption strip is always visible; and the table renders
 * `rows` in PAYLOAD order — phases → Externé → Systém — which is a backend contract.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { CostRow, CostRowKind, CostTotals, ManagerOverhead, ProjectCosts } from "@/types/metrics";

const { mockGetMetrics, mockListExternal } = vi.hoisted(() => ({
  mockGetMetrics: vi.fn(),
  mockListExternal: vi.fn(),
}));

vi.mock("@/services/api/metrics", () => ({ getProjectMetricsApi: mockGetMetrics }));

vi.mock("@/services/api/externalCost", () => ({
  listExternalCosts: mockListExternal,
  createExternalCost: vi.fn(),
  updateExternalCost: vi.fn(),
  deleteExternalCost: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useParams: () => ({ slug: "p1" }), useNavigate: () => vi.fn() };
});

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "p1", name: "Projekt 1" } }),
}));

import MetricsPage from "@/pages/MetricsPage";

// ─── payload fixtures (mirroring backend/services/metrics.py) ────────────────

/** Canonical COMPARISON_PHASES order — the payload order the screen must not re-sort. */
const PHASES = ["priprava", "navrh", "vizual", "programovanie", "verifikacia"];
const PHASE_LABELS = ["Príprava", "Návrh", "Vizuál", "Programovanie", "Verifikácia"];

const MANAGER: ManagerOverhead = { interventions: 2, wait_seconds: 120 };

function row(key: string, kind: CostRowKind, over: Partial<CostRow> = {}): CostRow {
  return {
    key,
    kind,
    turns: 3,
    input_tokens: 100,
    output_tokens: 50,
    share_pct: 15,
    agent_cost: null,
    unpriced_model_keys: [],
    human_minutes: null,
    human_cost: null,
    active_seconds: 600,
    ...over,
  };
}

function project(rows: CostRow[], totals: CostTotals, over: Partial<ProjectCosts> = {}): ProjectCosts {
  return {
    rows,
    totals,
    by_version: [
      {
        version_id: "v1",
        version_number: "1.0.0",
        rows,
        totals,
        manager: MANAGER,
        manager_wait_seconds: 120,
        internal_idle_seconds: null,
        total_time_seconds: null,
      },
    ],
    manager: MANAGER,
    coefficient_minutes_per_mtok: 600,
    wages: Object.fromEntries([...PHASES, "externe"].map((k) => [k, 12])),
    currency: "EUR",
    pricing_configured: true,
    coefficient_configured: true,
    wages_configured: true,
    ...over,
  };
}

// Everything configured: 5 measured phases (2 € each) + a hand-entered row (4 €) + the agent-only
// system row (1 €, human figures null by definition).
const CONFIGURED = project(
  [
    ...PHASES.map((p) => row(p, "phase", { agent_cost: 2, human_minutes: 60, human_cost: 12 })),
    row("externe", "external", {
      turns: 1,
      agent_cost: 4,
      human_minutes: 30,
      human_cost: 6,
      active_seconds: 0,
    }),
    row("system", "system", { turns: 2, agent_cost: 1, share_pct: 10 }),
  ],
  {
    turns: 18,
    turns_measured: 17, // 5 phases × 3 + the system row's 2
    turns_external: 1, // the entered row counts RECORDS, not agent messages
    input_tokens: 700,
    input_tokens_measured: 600,
    input_tokens_external: 100,
    output_tokens: 350,
    output_tokens_measured: 300,
    output_tokens_external: 50,
    agent_cost_measured: 11, // 5 phases × 2 + the system row's 1 — metered spend counts here
    agent_cost_external: 4,
    agent_cost_total: 15,
    human_minutes_measured: 300,
    human_minutes_external: 30,
    human_minutes_total: 330,
    human_cost_measured: 60,
    human_cost_external: 6,
    human_cost_total: 66,
  },
);

// Nothing configured: no coefficient, no prices, no wages, and no entered cost at all — the externals
// are a REAL 0.0 (nothing was entered), the measured halves are null (an input is unset).
const UNSET = project(
  [...PHASES.map((p) => row(p, "phase", { share_pct: 18 })), row("system", "system", { turns: 2, share_pct: 10 })],
  {
    turns: 17,
    turns_measured: 17,
    turns_external: 0,
    input_tokens: 600,
    input_tokens_measured: 600,
    input_tokens_external: 0,
    output_tokens: 300,
    output_tokens_measured: 300,
    output_tokens_external: 0,
    agent_cost_measured: null,
    agent_cost_external: 0,
    agent_cost_total: null,
    human_minutes_measured: null,
    human_minutes_external: 0,
    human_minutes_total: null,
    human_cost_measured: null,
    human_cost_external: 0,
    human_cost_total: null,
  },
  {
    coefficient_minutes_per_mtok: null,
    wages: Object.fromEntries([...PHASES, "externe"].map((k) => [k, null])),
    pricing_configured: false,
    coefficient_configured: false,
    wages_configured: false,
  },
);

// ─── DOM helpers ─────────────────────────────────────────────────────────────

// Column order (spec Part 5 §4): Fáza | ťahy | tokeny | podiel tokenov | Cena AI | Ľudský čas | Cena
// ľudskej práce. The footer split rows carry the SAME columns (no colSpan) — the split covers the
// counted figures too, not only money.
const COL_TURNS = 1;
const COL_TOKENS = 2;
const COL_AGENT_COST = 4;
const COL_HUMAN_COST = 6;

/** The active-scope table — rendered first; the per-version tables follow it. */
async function renderPage(payload: ProjectCosts): Promise<HTMLTableElement> {
  mockGetMetrics.mockResolvedValue(payload);
  render(<MetricsPage />);
  await screen.findByRole("heading", { name: /^Náklady —/ });
  const table = screen.getAllByRole("table")[0];
  if (!table) throw new Error("no cost table rendered");
  return table as HTMLTableElement;
}

function cell(r: HTMLTableRowElement, index: number): HTMLTableCellElement {
  const c = r.cells[index];
  if (!c) throw new Error(`row has no cell #${index}`);
  return c;
}

function text(r: HTMLTableRowElement, index: number): string {
  return cell(r, index).textContent ?? "";
}

/** The scope's cost rows in DOM order — the Manažér réžia row shares the tbody but is info-only. */
function costRows(table: HTMLTableElement): HTMLTableRowElement[] {
  const body = table.tBodies[0];
  if (!body) throw new Error("cost table has no tbody");
  return Array.from(body.rows).filter((r) => !text(r, 0).startsWith("Manažér"));
}

function rowAt(rows: HTMLTableRowElement[], index: number): HTMLTableRowElement {
  const r = rows[index];
  if (!r) throw new Error(`no cost row #${index}`);
  return r;
}

function footRow(table: HTMLTableElement, label: string): HTMLTableRowElement {
  const foot = table.tFoot;
  if (!foot) throw new Error("cost table has no tfoot");
  const r = Array.from(foot.rows).find((x) => text(x, 0) === label);
  if (!r) throw new Error(`no footer row "${label}"`);
  return r;
}

describe("MetricsPage — Náklady (CR-V2-063)", () => {
  beforeEach(() => {
    mockGetMetrics.mockReset();
    mockListExternal.mockReset();
    mockListExternal.mockResolvedValue([]);
  });

  // ── case 11 ────────────────────────────────────────────────────────────────

  it("renders a null human cost as — with no 0 in that cell, while a set one keeps its number", async () => {
    const table = await renderPage(CONFIGURED);
    const rows = costRows(table);

    // The agent-only system row has no human equivalent by definition.
    const systemCell = cell(rowAt(rows, 6), COL_HUMAN_COST);
    expect(systemCell.textContent).toBe("—");
    expect(systemCell.textContent).not.toMatch(/0/);

    // …and the dash is specific to the missing figure, not a blanket over the column.
    expect(text(rowAt(rows, 0), COL_HUMAN_COST)).toBe("12,00");
  });

  it("renders every human cost as — when the coefficient and wages are unset", async () => {
    const table = await renderPage(UNSET);
    for (const r of costRows(table)) {
      const c = cell(r, COL_HUMAN_COST);
      expect(c.textContent).toBe("—");
      expect(c.textContent).not.toMatch(/0/);
    }
  });

  // ── case 12 ────────────────────────────────────────────────────────────────

  it("marks the hand-entered row with a `ručne` badge and keeps it out of the measured subline", async () => {
    const table = await renderPage(CONFIGURED);
    const external = rowAt(costRows(table), 5);

    expect(within(cell(external, 0)).getByText("ručne")).toBeInTheDocument();
    expect(text(external, COL_AGENT_COST)).toBe("4,00");

    // Summed but never merged: the entered 4 € sits in "z toho ručne zadané" — the measured half
    // (11 €) does not contain it, and only the total (15 €) carries both.
    expect(text(footRow(table, "z toho namerané"), COL_AGENT_COST)).toBe("11,00");
    expect(text(footRow(table, "z toho ručne zadané"), COL_AGENT_COST)).toBe("4,00");
    expect(text(footRow(table, "Spolu"), COL_AGENT_COST)).toBe("15,00");

    // The headline cards carry the same split.
    expect(screen.getByText("z toho ručne zadané: 4,00 €")).toBeInTheDocument();
    expect(screen.getByText("z toho ručne zadané: 6,00 €")).toBeInTheDocument();
  });

  it("splits ťahy and tokeny in the footer too — a metered turn never merges with an entered record", async () => {
    const table = await renderPage(CONFIGURED);
    const measured = footRow(table, "z toho namerané");
    const external = footRow(table, "z toho ručne zadané");

    // No colSpan swallow: both halves carry their own counted figures, and they add up to "Spolu".
    expect(text(measured, COL_TURNS)).toBe("17");
    expect(text(external, COL_TURNS)).toBe("1");
    expect(text(footRow(table, "Spolu"), COL_TURNS)).toBe("18");

    expect(text(measured, COL_TOKENS)).toBe("600 / 300");
    expect(text(external, COL_TOKENS)).toBe("100 / 50");
    expect(text(footRow(table, "Spolu"), COL_TOKENS)).toBe("700 / 350");
  });

  // ── case 13 ────────────────────────────────────────────────────────────────

  it("shows the coefficient value in the assumption strip", async () => {
    await renderPage(CONFIGURED);
    expect(screen.getByText(/Ľudská práca je prepočet, nie meranie/)).toBeInTheDocument();
    expect(screen.getByText(/600 min na mil\. tokenov/)).toBeInTheDocument();
    // Nothing is unset, so the strip offers no Settings escape hatch.
    expect(screen.queryByRole("button", { name: "Nastavenia" })).toBeNull();
  });

  it("shows the not-configured wording and the settings link when the coefficient is null", async () => {
    await renderPage(UNSET);
    expect(screen.getByText(/Koeficient nie je nastavený — ľudské stĺpce sa nezobrazujú/)).toBeInTheDocument();
    expect(screen.queryByText(/Ľudská práca je prepočet, nie meranie/)).toBeNull();
    expect(screen.getByRole("button", { name: "Nastavenia" })).toBeInTheDocument();
  });

  // ── case 14 ────────────────────────────────────────────────────────────────

  it("renders the rows in payload order — phases, then Externé, then Systém last", async () => {
    const table = await renderPage(CONFIGURED);
    const labels = costRows(table).map((r) => text(r, 0));

    expect(labels).toHaveLength(7);
    expect(labels.slice(0, 5)).toEqual(PHASE_LABELS);
    expect(labels[5]).toMatch(/^Externé \(ručne zadané\)/); // the `ručne` badge shares the cell
    expect(labels[6]).toBe("Systém (neporovnané)");
  });
});
