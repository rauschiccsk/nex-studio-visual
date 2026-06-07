/**
 * Slovak display labels across the cockpit (CR-NS-018) — rail stages + role
 * labels on agent chips and message bubbles. Machine values are unchanged; only
 * the rendered label is Slovak.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineRail, { deriveActiveAgent } from "@/components/cockpit/PipelineRail";
import PipelineMessageBubble from "@/components/cockpit/PipelineMessageBubble";
import { nextStageLabel } from "@/components/cockpit/labels";
import type { ActivityLine, PipelineBoard, PipelineMessage, PipelineState } from "@/services/api/pipeline";

function mkState(): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: "build",
    current_actor: "implementer",
    status: "agent_working",
    next_action: "x",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-04T00:00:00Z",
    updated_at: "2026-06-04T00:00:00Z",
  };
}

describe("cockpit Slovak labels", () => {
  it("rail renders Slovak stage labels (not raw codes)", () => {
    render(<PipelineRail state={mkState()} />);
    expect(screen.getByText("Príprava")).toBeInTheDocument();
    expect(screen.getByText("Rozsah")).toBeInTheDocument();
    expect(screen.getByText("Programovanie")).toBeInTheDocument();
    expect(screen.getByText("Audit")).toBeInTheDocument();
    // raw codes are not shown as visible text (only as a tooltip)
    expect(screen.queryByText("Gate A")).not.toBeInTheDocument();
  });

  it("rail renders Slovak role labels on agent chips", () => {
    render(<PipelineRail state={mkState()} />);
    expect(screen.getByText("Koordinátor")).toBeInTheDocument();
    expect(screen.getByText("Návrhár")).toBeInTheDocument();
    expect(screen.getByText("Zákazník")).toBeInTheDocument();
    expect(screen.getByText("Programátor")).toBeInTheDocument();
    expect(screen.getByText("Audítor")).toBeInTheDocument();
    expect(screen.queryByText("Designer")).not.toBeInTheDocument();
  });

  it("message bubble renders Slovak author → recipient role labels", () => {
    const msg: PipelineMessage = {
      id: "33333333-3333-3333-3333-333333333333",
      version_id: "22222222-2222-2222-2222-222222222222",
      stage: "gate_a",
      author: "designer",
      recipient: "director",
      kind: "gate_report",
      content: "hotovo",
      status: "delivered",
      payload: null,
      created_at: "2026-06-04T00:00:00Z",
      seq: 1,
    };
    render(<PipelineMessageBubble message={msg} />);
    expect(screen.getByText("Návrhár")).toBeInTheDocument();
    expect(screen.getByText("Director")).toBeInTheDocument();
    expect(screen.queryByText("Designer")).not.toBeInTheDocument();
  });

  it("nextStageLabel returns the following stage's Slovak label (clamped at done)", () => {
    expect(nextStageLabel("kickoff")).toBe("Rozsah"); // gate_a
    expect(nextStageLabel("gate_e")).toBe("Plán úloh"); // task_plan (CR-NS-020 CR-2)
    expect(nextStageLabel("task_plan")).toBe("Programovanie"); // build
    expect(nextStageLabel("release")).toBe("Hotovo"); // done
    expect(nextStageLabel("done")).toBe("Hotovo"); // clamped
  });
});

describe("deriveActiveAgent (real active agent, not current_actor)", () => {
  const gateEState = (status: PipelineState["status"]): PipelineState => ({
    id: "1",
    version_id: "2",
    flow_type: "new_version",
    current_stage: "gate_e",
    current_actor: "customer", // nominal stage actor — must NOT win
    status,
    next_action: "x",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-06T00:00:00Z",
    updated_at: "2026-06-06T00:00:00Z",
  });
  const board = (state: PipelineState, messages: PipelineMessage[] = []): PipelineBoard => ({
    state,
    recent_messages: messages,
  });
  const msg = (author: PipelineMessage["author"]): PipelineMessage => ({
    id: author,
    version_id: "2",
    stage: "gate_e",
    author,
    recipient: "director",
    kind: "answer",
    content: "x",
    status: "delivered",
    payload: null,
    created_at: "2026-06-06T00:00:00Z",
    seq: 1,
  });

  it("while working = the latest activity frame's role (not the stage actor)", () => {
    const activity: ActivityLine[] = [
      { stage: "gate_e", actor: "customer", kind: "status", line: "pracuje…" },
      { stage: "gate_e", actor: "designer", kind: "status", line: "pracuje…" },
    ];
    expect(deriveActiveAgent(board(gateEState("agent_working")), activity)).toBe("designer");
  });

  it("while working with no activity falls back to current_actor", () => {
    expect(deriveActiveAgent(board(gateEState("agent_working")), [])).toBe("customer");
  });

  it("at awaiting_director = the latest message author (who just acted)", () => {
    expect(deriveActiveAgent(board(gateEState("awaiting_director"), [msg("coordinator")]), [])).toBe("coordinator");
  });

  it("ignores a system/director latest message at rest", () => {
    expect(deriveActiveAgent(board(gateEState("blocked"), [msg("system")]), [])).toBeNull();
  });
});
