/**
 * DEV-75 — in the task plan every task, feat and epic says what its delivered work is billed as, and the manager changes
 * it per task or for a whole feat or epic at once. A task without its own category counts as the version's.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import PlanUlohRail from "@/components/riadiace/PlanUlohRail";
import { setTaskCategories } from "@/services/api/deliveryStatements";
import { getTaskPlan } from "@/services/api/versions";
import type { PipelineBoard } from "@/services/api/pipeline";
import type { TaskPlanResponse, TaskPlanTaskNode } from "@/types/task-plan";

vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/deliveryStatements", () => ({ setTaskCategories: vi.fn() }));

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: MemoryRouter });

function task(id: string, number: number, billing_category: TaskPlanTaskNode["billing_category"]): TaskPlanTaskNode {
  return {
    id,
    number,
    title: `Úloha ${number}`,
    task_type: "backend",
    status: "todo",
    priority: "normal",
    checklist_type: null,
    description: "",
    plain_description: "",
    spend: null,
    billing_category,
  };
}

const PLAN: TaskPlanResponse = {
  epic_count: 1,
  feat_count: 1,
  task_count: 2,
  billing_default: "request",
  plan: [
    {
      id: "e1",
      number: 28,
      title: "Doručovanie",
      status: "in_progress",
      plain_description: "",
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Doručovateľ",
          status: "in_progress",
          description: "",
          plain_description: "",
          tasks: [task("t1", 1, null), task("t2", 2, "gift")],
        },
      ],
    },
  ],
};

function rail() {
  const board = { state: null, recent_messages: [], available_actions: [] } as unknown as PipelineBoard;
  return <PlanUlohRail versionId="v1" messages={[]} board={board} onBoard={() => {}} />;
}

beforeEach(() => {
  vi.mocked(getTaskPlan).mockReset().mockResolvedValue(PLAN);
  vi.mocked(setTaskCategories).mockReset().mockResolvedValue({ changed: 1 });
});

describe("the task plan — what each task is billed as (DEV-75)", () => {
  it("shows each task's own category, the version's for one without, and „rôzne“ for a mixed feat and epic", async () => {
    render(rail());

    const own = await screen.findByLabelText("Kategória úlohy 28.1.2");
    expect(own).toHaveValue("gift");
    const none = screen.getByLabelText("Kategória úlohy 28.1.1");
    expect(none).toHaveValue("");
    expect(none).toHaveDisplayValue("podľa verzie (požiadavka zákazníka)");
    expect(screen.getByLabelText("Kategória všetkých úloh funkcie 28.1")).toHaveDisplayValue("rôzne");
    expect(screen.getByLabelText("Kategória všetkých úloh celku 28")).toHaveDisplayValue("rôzne");
  });

  it("changes one task, then reads the plan again", async () => {
    render(rail());

    fireEvent.change(await screen.findByLabelText("Kategória úlohy 28.1.1"), { target: { value: "bug" } });

    await waitFor(() => expect(setTaskCategories).toHaveBeenCalledWith("v1", ["t1"], "bug"));
    await waitFor(() => expect(getTaskPlan).toHaveBeenCalledTimes(2));
  });

  it("sets every task of a feat or an epic at once, and back to the version's", async () => {
    render(rail());

    fireEvent.change(await screen.findByLabelText("Kategória všetkých úloh funkcie 28.1"), {
      target: { value: "gift" },
    });
    await waitFor(() => expect(setTaskCategories).toHaveBeenCalledWith("v1", ["t1", "t2"], "gift"));

    fireEvent.change(screen.getByLabelText("Kategória všetkých úloh celku 28"), { target: { value: "" } });
    await waitFor(() => expect(setTaskCategories).toHaveBeenLastCalledWith("v1", ["t1", "t2"], null));
  });

  it("says when the change failed and keeps the plan", async () => {
    vi.mocked(setTaskCategories).mockRejectedValue(new Error("Niektorá z úloh nepatrí tejto verzii — obnov stránku."));
    render(rail());

    fireEvent.change(await screen.findByLabelText("Kategória úlohy 28.1.1"), { target: { value: "bug" } });

    expect(await screen.findByText("Kategóriu úloh sa nepodarilo uložiť — skús to prosím znova.")).toBeInTheDocument();
    expect(screen.getByLabelText("Kategória úlohy 28.1.2")).toBeInTheDocument();
  });
});
