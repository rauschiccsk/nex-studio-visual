/**
 * DEV-210 — the task plan says how long the agent will work, as the cockpit forecast it from the agent's measured
 * work; with nothing measured to go by it says nothing rather than a guess. 23.10.2026 the plan of NEX Manager 1.4.0
 * said „~54 h“ — the human-developer minutes the agent used to write — for about an hour of the agent's work.
 * Director: „Ja potrebujem odhad, že agent približne koľko to bude robiť.“
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import PlanUlohRail from "@/components/riadiace/PlanUlohRail";
import { getTaskPlan } from "@/services/api/versions";
import type { PipelineBoard } from "@/services/api/pipeline";
import type { TaskPlanResponse } from "@/types/task-plan";

vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

function task(id: string, status: "done" | "todo") {
  return {
    id,
    number: Number(id.slice(1)),
    title: `Úloha ${id}`,
    task_type: "backend",
    status,
    priority: "normal",
    checklist_type: null,
    description: "",
    plain_description: "",
    spend: null,
  };
}

function plan(forecast: TaskPlanResponse["forecast"]): TaskPlanResponse {
  return {
    epic_count: 1,
    feat_count: 1,
    task_count: 2,
    forecast,
    plan: [
      {
        id: "e1",
        number: 1,
        title: "Skupiny",
        status: "in_progress",
        plain_description: "",
        feats: [
          {
            id: "f1",
            number: 1,
            title: "Databáza",
            status: "in_progress",
            description: "",
            plain_description: "",
            tasks: [task("t1", "done"), task("t2", "todo")],
          },
        ],
      },
    ],
  };
}

function rail() {
  const board = { state: null, recent_messages: [], available_actions: [] } as unknown as PipelineBoard;
  return render(
    <MemoryRouter>
      <PlanUlohRail versionId="v1" messages={[]} board={board} onBoard={() => {}} />
    </MemoryRouter>,
  );
}

describe("Plán úloh — how long the agent will work (DEV-210)", () => {
  it("says the cockpit's forecast and what is left", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(
      plan({ text: "asi 40 min ± 10 %", remaining_text: "zostáva asi 25 min" }),
    );
    rail();

    expect(await screen.findByText("Práca agenta: asi 40 min ± 10 % · zostáva asi 25 min")).toBeInTheDocument();
  });

  it("says no figure at all when there is nothing measured to go by", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(plan(null));
    rail();

    expect(await screen.findByText("1/2 úloh hotových")).toBeInTheDocument();
    expect(screen.queryByText(/Práca agenta/)).toBeNull();
  });
});
