/**
 * Component tests for TaskPlanPanel (CR-NS-020 CR-5).
 *
 * Renders the EPIC→FEAT→TASK tree (with per-node status) from getTaskPlan, and on a task
 * click shows the per-task audit verdict matched from the live message stream by
 * payload.task_id (the Auditor turn's tag).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import TaskPlanPanel from "@/components/cockpit/TaskPlanPanel";
import type { PipelineMessage } from "@/services/api/pipeline";
import type { TaskPlanResponse, TaskNodeStatus } from "@/types/task-plan";
import { getTaskPlan } from "@/services/api/versions";

vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));

const PLAN: TaskPlanResponse = {
  plan: [
    {
      id: "e1",
      number: 1,
      title: "Foundation",
      status: "in_progress",
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Schema",
          status: "in_progress",
          tasks: [
            { id: "t1", number: 1, title: "GL tables", task_type: "migration", status: "in_progress", priority: "normal", checklist_type: null, description: "" },
            { id: "t2", number: 2, title: "AP tables", task_type: "migration", status: "todo", priority: "normal", checklist_type: null, description: "" },
          ],
        },
      ],
    },
  ],
  epic_count: 1,
  feat_count: 1,
  task_count: 2,
};

function mkMsg(over: Partial<PipelineMessage>): PipelineMessage {
  return {
    id: `m${Math.random()}`,
    version_id: "v1",
    stage: "build",
    author: "auditor",
    recipient: "director",
    kind: "gate_report",
    content: "",
    status: "delivered",
    payload: {},
    created_at: "2026-06-08T00:00:00Z",
    seq: 1,
    ...over,
  };
}

describe("TaskPlanPanel (CR-NS-020 CR-5)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("renders the EPIC→FEAT→TASK tree with status labels", async () => {
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText(/Foundation/)).toBeInTheDocument();
    expect(screen.getByText(/Schema/)).toBeInTheDocument();
    expect(screen.getByText(/GL tables/)).toBeInTheDocument();
    expect(screen.getByText(/AP tables/)).toBeInTheDocument();
    expect(screen.getAllByText("Prebieha").length).toBeGreaterThan(0); // in_progress
    expect(screen.getByText("Čaká")).toBeInTheDocument(); // todo
  });

  it("shows the per-task audit verdict matched by payload.task_id on task click", async () => {
    const messages = [
      mkMsg({ author: "auditor", stage: "build", payload: { task_id: "t1", task_pass: false, findings: ["chýba podvojnosť"] } }),
    ];
    render(<TaskPlanPanel versionId="v1" messages={messages} />);
    const task = await screen.findByText(/GL tables/);
    task.closest("button")!.click();
    expect(await screen.findByText("Audit FAIL")).toBeInTheDocument();
    expect(screen.getByText("chýba podvojnosť")).toBeInTheDocument();
  });
});

function planWith(statuses: TaskNodeStatus[]): TaskPlanResponse {
  return {
    plan: [
      {
        id: "e1",
        number: 1,
        title: "Epic",
        status: "in_progress",
        feats: [
          {
            id: "f1",
            number: 1,
            title: "Feat",
            status: "in_progress",
            tasks: statuses.map((status, i) => ({
              id: `t${i + 1}`,
              number: i + 1,
              title: `Task ${i + 1}`,
              task_type: "backend",
              status,
              priority: "normal",
              checklist_type: null,
              description: "",
            })),
          },
        ],
      },
    ],
    epic_count: 1,
    feat_count: 1,
    task_count: statuses.length,
  };
}

const EMPTY_PLAN: TaskPlanResponse = { plan: [], epic_count: 0, feat_count: 0, task_count: 0 };

describe("TaskPlanPanel — build progress indicator (CR-NS-025 Part 2)", () => {
  it("renders the % of tasks done for a mixed plan (3/8 → 38 %)", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(
      planWith(["done", "done", "done", "in_progress", "todo", "todo", "todo", "todo"]),
    );
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText(/Postup: 3\/8 úloh \(38 %\)/)).toBeInTheDocument();
    expect(screen.getByTestId("taskplan-progress-fill")).toHaveClass("bg-amber-400"); // <100 → amber
  });

  it("shows 100 % and a green bar when all tasks are done", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(planWith(["done", "done", "done"]));
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText(/Postup: 3\/3 úloh \(100 %\)/)).toBeInTheDocument();
    expect(screen.getByTestId("taskplan-progress-fill")).toHaveClass("bg-emerald-500"); // 100 → green
  });

  it("surfaces the failed count in red when any task failed", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(planWith(["done", "failed", "todo", "failed"]));
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText(/Postup: 1\/4 úloh \(25 %\)/)).toBeInTheDocument();
    expect(screen.getByText(/· 2 zlyhané/)).toBeInTheDocument();
  });

  it("hides the indicator when there is no plan", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(EMPTY_PLAN);
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText("Plán úloh ešte nebol vytvorený.")).toBeInTheDocument();
    expect(screen.queryByText(/Postup:/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("taskplan-progress-fill")).not.toBeInTheDocument();
  });

  it("hides the indicator when a plan has epics but zero tasks (no '0/0' state)", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(planWith([])); // one epic/feat, no tasks → totalCount 0
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText(/Epic/)).toBeInTheDocument(); // the tree still renders the epic
    expect(screen.queryByText(/Postup:/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("taskplan-progress-fill")).not.toBeInTheDocument();
  });

  it("hides the indicator on a fetch error (consistent with the tree's error state)", async () => {
    vi.mocked(getTaskPlan).mockRejectedValue(new Error("Načítanie plánu zlyhalo"));
    render(<TaskPlanPanel versionId="v1" messages={[]} />);
    expect(await screen.findByText("Načítanie plánu zlyhalo")).toBeInTheDocument();
    expect(screen.queryByText(/Postup:/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("taskplan-progress-fill")).not.toBeInTheDocument();
  });
});
