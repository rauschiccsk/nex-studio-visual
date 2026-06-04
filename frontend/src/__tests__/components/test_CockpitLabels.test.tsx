/**
 * Slovak display labels across the cockpit (CR-NS-018) — rail stages + role
 * labels on agent chips and message bubbles. Machine values are unchanged; only
 * the rendered label is Slovak.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineRail from "@/components/cockpit/PipelineRail";
import PipelineMessageBubble from "@/components/cockpit/PipelineMessageBubble";
import type { PipelineMessage, PipelineState } from "@/services/api/pipeline";

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
    };
    render(<PipelineMessageBubble message={msg} />);
    expect(screen.getByText("Návrhár")).toBeInTheDocument();
    expect(screen.getByText("Director")).toBeInTheDocument();
    expect(screen.queryByText("Designer")).not.toBeInTheDocument();
  });
});
