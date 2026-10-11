/**
 * DEV-49 — the plan shows, at every EPIC, FEAT and TASK, how long the agent worked, what it cost and its tokens.
 *
 * Director 10.10.2026: „Chcel by som niečo podobné pre každý EPIC, FEAT a TASK napríklad takto: ‚Trvalo: 26 s
 * cena: 0,68 € 1200 tokenov'.“ The figures come from the backend (metrics.node_spend); these pin how the screen
 * says them — and that a price with a gap reads „aspoň“, never as the whole.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import { PlanUlohRail } from "@/components/riadiace/PlanUlohRail";
import { formatTokens, spendDetail, spendLine } from "@/lib/spend";
import { getTaskPlan } from "@/services/api/versions";
import type { PipelineBoard } from "@/services/api/pipeline";
import type { NodeSpend, TaskPlanResponse } from "@/types/task-plan";

vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: MemoryRouter });

function spend(over: Partial<NodeSpend> = {}): NodeSpend {
  return {
    seconds: 684,
    turns: 1,
    tokens: {
      input: 94,
      output: 63839,
      cache_read: 12120634,
      cache_write: 102268,
      total: 12286835,
    },
    eur: 4.05,
    eur_complete: true,
    unpriced: [],
    ...over,
  };
}

describe("spend line — as the screen words it", () => {
  it("task 2.1.3 of Career Asistent, as the Director got it in the proposal", () => {
    expect(spendLine(spend())).toBe(
      "Trvalo 11 min 24 s · cena 4,05 € · 12,3 mil. tokenov",
    );
  });

  it("⚠️ a price with a gap is „at least“, not the whole", () => {
    expect(spendLine(spend({ eur: 3.29, eur_complete: false }))).toContain(
      "cena aspoň 3,29 €",
    );
    expect(spendLine(spend({ eur: null, eur_complete: false }))).toContain(
      "cena sa nedá vyčísliť",
    );
  });

  it.each([
    [1, "1 token"],
    [3, "3 tokeny"],
    [850, "850 tokenov"],
    [12_300, "12,3 tis. tokenov"],
    [3_096_000, "3,1 mil. tokenov"],
  ])("%i → %s", (n, text) => {
    expect(formatTokens(n)).toBe(text);
  });

  it("on hover breaks the tokens down as Náklady and says why the price is not the whole", () => {
    const detail = spendDetail(
      spend({
        eur_complete: false,
        unpriced: ["cenník modelu claude-haiku-5-5 sa zatiaľ nedá zistiť"],
      }),
    );

    expect(detail).toContain("výstup 63");
    expect(detail).toContain("čítanie z vyrovnávacej pamäte 12");
    expect(detail).toContain("Cena je neúplná: cenník modelu claude-haiku-5-5");
  });
});

const PLAN: TaskPlanResponse = {
  epic_count: 1,
  feat_count: 1,
  task_count: 2,
  plan: [
    {
      id: "e1",
      number: 2,
      title: "Ponuky",
      status: "in_progress",
      plain_description: "",
      spend: spend({
        seconds: 1600,
        eur: 7.25,
        eur_complete: false,
        tokens: { ...spend().tokens, total: 20_000_000 },
      }),
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Sťahovanie",
          status: "in_progress",
          description: "",
          plain_description: "",
          spend: spend({ seconds: 1600, eur: 7.25, eur_complete: false }),
          tasks: [
            {
              id: "t1",
              number: 3,
              title: "Detail ponuky",
              task_type: "backend",
              status: "done",
              priority: "normal",
              checklist_type: null,
              description: "",
              plain_description: "",
              spend: spend(),
            },
            {
              id: "t2",
              number: 4,
              title: "Nezačatá",
              task_type: "backend",
              status: "todo",
              priority: "normal",
              checklist_type: null,
              description: "",
              plain_description: "",
              spend: null,
            },
          ],
        },
      ],
    },
  ],
};

function rail() {
  const board = {
    state: null,
    recent_messages: [],
    available_actions: [],
  } as unknown as PipelineBoard;
  return (
    <PlanUlohRail
      versionId="v1"
      messages={[]}
      board={board}
      onBoard={() => {}}
    />
  );
}

describe("Plán úloh — spend at every node", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("task, FEAT and EPIC carry their own line; a task not started carries none", async () => {
    render(rail());

    expect(await screen.findByTestId("planrail-spend-t1")).toHaveTextContent(
      "Trvalo 11 min 24 s · cena 4,05 € · 12,3 mil. tokenov",
    );
    expect(screen.getByTestId("planrail-spend-f1")).toHaveTextContent(
      "cena aspoň 7,25 €",
    );
    expect(screen.getByTestId("planrail-spend-e1")).toHaveTextContent(
      "Trvalo 26 min 40 s · cena aspoň 7,25 € · 20,0 mil. tokenov",
    );
    expect(screen.queryByTestId("planrail-spend-t2")).not.toBeInTheDocument();
  });

  it("a collapsed EPIC still shows its total", async () => {
    render(rail());
    fireEvent.click(await screen.findByTestId("planrail-chevron-e1"));

    expect(screen.queryByTestId("planrail-spend-t1")).not.toBeInTheDocument();
    expect(screen.getByTestId("planrail-spend-e1")).toBeInTheDocument();
  });
});
