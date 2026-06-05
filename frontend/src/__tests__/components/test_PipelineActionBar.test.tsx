/**
 * Component tests for PipelineActionBar (CR-NS-018 — gate action clarity).
 *
 * kickoff is a ratification gate: at (kickoff, awaiting) the Director must see
 * Schváliť podľa Návrhára + Vrátiť (engine's approve advances kickoff→gate_a),
 * NOT the dead "Spustiť" button. Each primary action carries a consequence line,
 * and "Schváliť návrh Koordinátora" appears only when a Coordinator report exists.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineActionBar from "@/components/cockpit/PipelineActionBar";
import type { PipelineState, PipelineStage, PipelineStatus } from "@/services/api/pipeline";

const APPROVE = "Schváliť podľa Návrhára";
const COORD = "Schváliť návrh Koordinátora";

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

describe("PipelineActionBar — gate action clarity", () => {
  it("at (kickoff, awaiting) shows Schváliť podľa Návrhára + Vrátiť, never Spustiť", () => {
    render(<PipelineActionBar state={mkState("kickoff", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText(APPROVE)).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
    expect(screen.queryByText("Spustiť")).not.toBeInTheDocument();
  });

  it("at (gate_a, awaiting) still shows Schváliť podľa Návrhára + Vrátiť", () => {
    render(<PipelineActionBar state={mkState("gate_a", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText(APPROVE)).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
  });

  it("renders a consequence line naming the next stage under Schváliť", () => {
    render(<PipelineActionBar state={mkState("gate_a", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    // gate_a → gate_b = "Rozhranie (API)"
    expect(screen.getByText(/spustí sa ďalšia fáza \(Rozhranie \(API\)\)/)).toBeInTheDocument();
  });

  it("relabeled approve still fires the 'approve' action", () => {
    const onAction = vi.fn();
    render(<PipelineActionBar state={mkState("gate_a", "awaiting_director")} inFlight={false} onAction={onAction} />);
    screen.getByText(APPROVE).click();
    expect(onAction).toHaveBeenCalledWith("approve");
  });

  it("hides 'Schváliť návrh Koordinátora' when there is no Coordinator report", () => {
    render(<PipelineActionBar state={mkState("gate_a", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.queryByText(COORD)).not.toBeInTheDocument();
  });

  it("shows 'Schváliť návrh Koordinátora' when a report exists and fires the new action", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("gate_a", "awaiting_director")}
        inFlight={false}
        hasCoordinatorReport
        onAction={onAction}
      />,
    );
    expect(screen.getByText(COORD)).toBeInTheDocument();
    screen.getByText(COORD).click();
    expect(onAction).toHaveBeenCalledWith("apply_coordinator_recommendation");
  });

  it("at (gate_g, awaiting) shows the PASS/FAIL verdict, not Schváliť", () => {
    render(<PipelineActionBar state={mkState("gate_g", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Verdikt PASS")).toBeInTheDocument();
    expect(screen.getByText("Verdikt FAIL")).toBeInTheDocument();
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
  });

  it("while agent_working shows Pauza (no ratify buttons)", () => {
    render(<PipelineActionBar state={mkState("kickoff", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Pauza")).toBeInTheDocument();
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
    expect(screen.queryByText("Spustiť")).not.toBeInTheDocument();
  });

  it("question-block (agent asking) shows Odpoveď + Schváliť + Vrátiť (never a dead-end)", () => {
    render(<PipelineActionBar state={mkState("kickoff", "blocked")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Odpoveď")).toBeInTheDocument();
    expect(screen.getByText(APPROVE)).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
    expect(screen.getByText("Otázka")).toBeInTheDocument();
    expect(screen.queryByText("Skús znova")).not.toBeInTheDocument();
  });

  it("does not show 'Schváliť návrh Koordinátora' on a question-block (only awaiting ratify)", () => {
    render(
      <PipelineActionBar
        state={mkState("kickoff", "blocked")}
        inFlight={false}
        hasCoordinatorReport
        onAction={vi.fn()}
      />,
    );
    expect(screen.queryByText(COORD)).not.toBeInTheDocument();
  });

  it("error-block (agent crash) shows Skús znova, not Schváliť/Odpoveď", () => {
    render(
      <PipelineActionBar state={mkState("gate_b", "blocked")} inFlight={false} isErrorBlock onAction={vi.fn()} />,
    );
    expect(screen.getByText("Skús znova")).toBeInTheDocument();
    expect(screen.getByText("Otázka")).toBeInTheDocument();
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
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

describe("PipelineActionBar — Gate E boundary (Phase 3)", () => {
  it("topic boundary shows continue + Ukončiť Gate E, not the generic ratify buttons", () => {
    render(
      <PipelineActionBar state={mkState("gate_e", "awaiting_director")} inFlight={false} onAction={vi.fn()} />,
    );
    expect(screen.getByText("Schváliť okruh a pokračovať")).toBeInTheDocument();
    expect(screen.getByText("Ukončiť Gate E")).toBeInTheDocument();
    // not the generic gate_a–d labels, and never the Coordinator button at gate_e
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
    expect(screen.queryByText(COORD)).not.toBeInTheDocument();
    expect(screen.queryByText("Finálne schválenie → Programovanie")).not.toBeInTheDocument();
  });

  it("topic-boundary approve fires the plain approve (continue topic)", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar state={mkState("gate_e", "awaiting_director")} inFlight={false} onAction={onAction} />,
    );
    screen.getByText("Schváliť okruh a pokračovať").click();
    expect(onAction).toHaveBeenCalledWith("approve");
  });

  it("Ukončiť Gate E is disabled while findings are open", () => {
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateEOpenFindings={2}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Ukončiť Gate E").closest("button")).toBeDisabled();
  });

  it("Ukončiť Gate E fires end_gate_e when no open findings", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar state={mkState("gate_e", "awaiting_director")} inFlight={false} onAction={onAction} />,
    );
    screen.getByText("Ukončiť Gate E").click();
    expect(onAction).toHaveBeenCalledWith("end_gate_e");
  });

  it("final boundary (coverage complete, no open findings) shows enabled final sign-off", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateECoverageComplete
        onAction={onAction}
      />,
    );
    const final = screen.getByText("Finálne schválenie → Programovanie");
    expect(final.closest("button")).not.toBeDisabled();
    expect(screen.queryByText("Schváliť okruh a pokračovať")).not.toBeInTheDocument();
    final.click();
    expect(onAction).toHaveBeenCalledWith("approve");
  });

  it("final sign-off is disabled while findings are open", () => {
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateECoverageComplete
        gateEOpenFindings={1}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Finálne schválenie → Programovanie").closest("button")).toBeDisabled();
  });

  it("mid-round policy pause (blocked) shows Rozhodni politiku", () => {
    const onAction = vi.fn();
    render(<PipelineActionBar state={mkState("gate_e", "blocked")} inFlight={false} onAction={onAction} />);
    expect(screen.getByText("Rozhodni politiku")).toBeInTheDocument();
    // not the generic question-block ratify
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
  });
});
