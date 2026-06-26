/**
 * TaskSummaryCard rendering (CR-NS-054 Pillar C §C.3).
 *
 * Compact header always (task # + title + status + attempt badge); expand → čo urobené + audit verdikt +
 * per-attempt error drill-down.
 */

import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import TaskSummaryCard from "@/components/cockpit/TaskSummaryCard";
import type { PipelineMessage } from "@/services/api/pipeline";

function mkSummary(taskSummary: Record<string, unknown>): PipelineMessage {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    stage: "build",
    author: "system",
    recipient: "manazer",
    kind: "notification",
    content: "Úloha #5 — hotovo",
    status: "delivered",
    payload: { is_task_summary: true, task_summary: taskSummary },
    created_at: "2026-06-13T00:00:00Z",
    seq: 1,
  };
}

const DONE = {
  task_id: "t1",
  task_number: 5,
  title: "Auth modul",
  final_status: "done",
  attempts: 2,
  audit_verdict: { task_pass: true, findings: [] },
  last_error: null,
  work_summary: "Implementoval som **JWT** login.",
  attempt_errors: ["audit zlyhal: chýba test"],
};

const FAILED = {
  task_id: "t2",
  task_number: 7,
  title: "Broken modul",
  final_status: "failed",
  attempts: 5,
  audit_verdict: { task_pass: false, findings: ["chýba validácia DPH"] },
  last_error: "audit zlyhal: chýba validácia DPH",
  work_summary: null,
  attempt_errors: ["e1", "e2", "e3", "e4", "audit zlyhal: chýba validácia DPH"],
};

describe("TaskSummaryCard (CR-NS-054 §C.3)", () => {
  it("compact header: task # + title + done status + attempt badge; expand content hidden by default", () => {
    render(<TaskSummaryCard message={mkSummary(DONE)} />);
    expect(screen.getByText("#5")).toBeInTheDocument();
    expect(screen.getByText("Auth modul")).toBeInTheDocument();
    expect(screen.getByText(/hotovo · 2 pokusy/)).toBeInTheDocument();
    // collapsed by default — expand sections not shown
    expect(screen.queryByText("Čo urobené")).not.toBeInTheDocument();
    expect(screen.queryByText("Audit")).not.toBeInTheDocument();
  });

  it("expands to reveal čo urobené + audit verdikt + per-attempt drill-down", () => {
    render(<TaskSummaryCard message={mkSummary(FAILED)} />);
    fireEvent.click(screen.getByText("Broken modul"));
    // (a) work_summary is null here → no "Čo urobené"; (b) audit verdikt shown
    expect(screen.getByText("Audit")).toBeInTheDocument();
    expect(screen.getByText("chýba validácia DPH")).toBeInTheDocument();
    // (c) per-pokus drill-down: every failed attempt's error
    expect(screen.getByText(/Pokusy \(5/)).toBeInTheDocument();
    expect(screen.getByText(/5\. audit zlyhal: chýba validácia DPH/)).toBeInTheDocument();
  });

  it("renders the Implementer's work summary (markdown) on expand for a done task", () => {
    render(<TaskSummaryCard message={mkSummary(DONE)} />);
    fireEvent.click(screen.getByText("Auth modul"));
    expect(screen.getByText("Čo urobené")).toBeInTheDocument();
    expect(screen.getByText("JWT")).toBeInTheDocument(); // bold markdown rendered
    expect(screen.getByText("Prešiel")).toBeInTheDocument(); // audit pass verdikt
  });

  it("renders nothing for a message without task_summary (defensive)", () => {
    const { container } = render(
      <TaskSummaryCard
        message={{ ...mkSummary({}), payload: { is_task_summary: true } }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
