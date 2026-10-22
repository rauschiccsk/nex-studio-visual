/**
 * DEV-84 — a link to one task (`?uloha=28.1.2` from the repository tab) opens its epic and feat in the task plan, scrolls
 * to the task and outlines it; the manager's own collapse choice stays as it was.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import PlanUlohRail from "@/components/riadiace/PlanUlohRail";
import { getTaskPlan } from "@/services/api/versions";
import type { PipelineBoard } from "@/services/api/pipeline";
import type { TaskPlanResponse, TaskPlanTaskNode } from "@/types/task-plan";

vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/deliveryStatements", () => ({ setTaskCategories: vi.fn() }));

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: MemoryRouter });

function task(id: string, number: number): TaskPlanTaskNode {
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
    billing_category: null,
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
      status: "planned",
      plain_description: "",
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Doručovateľ",
          status: "todo",
          description: "",
          plain_description: "",
          tasks: [task("t1", 1), task("t2", 2)],
        },
      ],
    },
  ],
};

function rail(focusTask?: string) {
  const board = { state: null, recent_messages: [], available_actions: [] } as unknown as PipelineBoard;
  return <PlanUlohRail versionId="v1" messages={[]} board={board} onBoard={() => {}} focusTask={focusTask} />;
}

const scrolled = vi.fn();

beforeEach(() => {
  window.localStorage.clear();
  // The manager collapsed the epic earlier.
  window.localStorage.setItem("nex_planrail_collapsed_v1", JSON.stringify(["e1"]));
  vi.mocked(getTaskPlan).mockReset().mockResolvedValue(PLAN);
  scrolled.mockReset();
  Element.prototype.scrollIntoView = scrolled;
});

describe("a link to one task in the task plan (DEV-84)", () => {
  it("opens the collapsed epic, scrolls to the task and outlines it", async () => {
    render(rail("28.1.2"));

    const title = await screen.findByText("2. Úloha 2");
    const node = document.getElementById("planrail-task-t2");
    expect(node).toContainElement(title);
    expect(node?.className).toContain("ring-2");
    expect(document.getElementById("planrail-task-t1")?.className).not.toContain("ring-2");
    // The scroll is an effect after the render that shows the task — waited for, not read at once.
    await waitFor(() => expect(scrolled).toHaveBeenCalledWith({ block: "center" }));
    expect(scrolled.mock.contexts[0]).toBe(node);
    expect(JSON.parse(window.localStorage.getItem("nex_planrail_collapsed_v1") ?? "[]")).toEqual(["e1"]);
  });

  it("without a link the epic the manager collapsed stays collapsed", async () => {
    render(rail());

    await screen.findByText("28. Doručovanie");
    expect(screen.queryByText("2. Úloha 2")).not.toBeInTheDocument();
    expect(scrolled).not.toHaveBeenCalled();
  });

  it("a task the version's plan does not have is said, not silently ignored", async () => {
    render(rail("99.1.1"));

    expect(await screen.findByText("Úloha 99.1.1 v pláne tejto verzie nie je.")).toBeInTheDocument();
  });
});
