/**
 * Component tests for PipelineActionBar (CR-NS-018 fix — kickoff ratification).
 *
 * kickoff is a ratification gate: at (kickoff, awaiting) the Director must see
 * Schváliť + Vrátiť (engine's approve advances kickoff→gate_a), NOT the dead
 * "Spustiť" button (start is rejected once the pipeline exists).
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineActionBar from "@/components/cockpit/PipelineActionBar";
import type { PipelineState, PipelineStage, PipelineStatus } from "@/services/api/pipeline";

function mkState(stage: PipelineStage, status: PipelineStatus): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: stage,
    current_actor: "coordinator",
    status,
    next_action: "x",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-04T00:00:00Z",
    updated_at: "2026-06-04T00:00:00Z",
  };
}

describe("PipelineActionBar — kickoff ratification", () => {
  it("at (kickoff, awaiting) shows Schváliť + Vrátiť, never Spustiť", () => {
    render(<PipelineActionBar state={mkState("kickoff", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Schváliť")).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
    expect(screen.queryByText("Spustiť")).not.toBeInTheDocument();
  });

  it("at (gate_a, awaiting) still shows Schváliť + Vrátiť", () => {
    render(<PipelineActionBar state={mkState("gate_a", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Schváliť")).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
  });

  it("at (gate_g, awaiting) shows the PASS/FAIL verdict, not Schváliť", () => {
    render(<PipelineActionBar state={mkState("gate_g", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Verdikt PASS")).toBeInTheDocument();
    expect(screen.getByText("Verdikt FAIL")).toBeInTheDocument();
    expect(screen.queryByText("Schváliť")).not.toBeInTheDocument();
  });

  it("while agent_working shows Pauza (no ratify buttons)", () => {
    render(<PipelineActionBar state={mkState("kickoff", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Pauza")).toBeInTheDocument();
    expect(screen.queryByText("Schváliť")).not.toBeInTheDocument();
    expect(screen.queryByText("Spustiť")).not.toBeInTheDocument();
  });

  it("question-block (agent asking) shows Odpoveď + Schváliť + Vrátiť (never a dead-end)", () => {
    render(<PipelineActionBar state={mkState("kickoff", "blocked")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Odpoveď")).toBeInTheDocument();
    expect(screen.getByText("Schváliť")).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
    expect(screen.getByText("Otázka")).toBeInTheDocument();
    expect(screen.queryByText("Skús znova")).not.toBeInTheDocument();
  });

  it("error-block (agent crash) shows Skús znova, not Schváliť/Odpoveď", () => {
    render(
      <PipelineActionBar state={mkState("gate_b", "blocked")} inFlight={false} isErrorBlock onAction={vi.fn()} />,
    );
    expect(screen.getByText("Skús znova")).toBeInTheDocument();
    expect(screen.getByText("Otázka")).toBeInTheDocument();
    expect(screen.queryByText("Schváliť")).not.toBeInTheDocument();
    expect(screen.queryByText("Vrátiť")).not.toBeInTheDocument();
    expect(screen.queryByText("Odpoveď")).not.toBeInTheDocument();
  });

  it("Skús znova re-dispatches the current stage via return", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar state={mkState("gate_b", "blocked")} inFlight={false} isErrorBlock onAction={onAction} />,
    );
    screen.getByText("Skús znova").click();
    expect(onAction).toHaveBeenCalledWith("return", { comment: "Skús znova." });
  });
});
