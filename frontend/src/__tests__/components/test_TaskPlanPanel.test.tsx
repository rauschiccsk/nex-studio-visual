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
import type { TaskPlanResponse } from "@/types/task-plan";
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
