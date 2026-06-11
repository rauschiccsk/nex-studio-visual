/**
 * WhosTurnBoard (WS-C2, CR-NS-035): whose turn + decision-type + relay chain + current task +
 * Coordinator proposal — derived honestly from the live state + available_actions.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import WhosTurnBoard from "@/components/cockpit/WhosTurnBoard";
import type {
  PipelineState,
  PipelineStage,
  PipelineActor,
  PipelineStatus,
  CoordinatorDirective,
} from "@/services/api/pipeline";

function mkState(stage: PipelineStage, actor: PipelineActor, status: PipelineStatus): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: stage,
    current_actor: actor,
    status,
    next_action: "",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
  };
}

describe("WhosTurnBoard (WS-C2, CR-NS-035)", () => {
  it("a ratify gate awaiting → Director's turn + 'Schváliť alebo vrátiť', no relay breadcrumb", () => {
    render(<WhosTurnBoard state={mkState("gate_a", "designer", "awaiting_director")} availableActions={["approve", "return", "ask"]} />);
    expect(screen.getByText("Na rade: Director")).toBeInTheDocument();
    expect(screen.getByText("Schváliť alebo vrátiť")).toBeInTheDocument();
    expect(screen.queryByText("cez Koordinátora")).not.toBeInTheDocument(); // gate_a is not relayed
  });

  it("build awaiting → relay breadcrumb (cez Koordinátora) + current task + build decision", () => {
    render(
      <WhosTurnBoard
        state={mkState("build", "implementer", "awaiting_director")}
        availableActions={["approve", "continue_build", "return", "end_build", "ask"]}
        currentTask={{ number: 3, title: "AP tables" }}
      />,
    );
    expect(screen.getByText("cez Koordinátora")).toBeInTheDocument(); // build IS relayed
    expect(screen.getByText(/úloha #3:/)).toBeInTheDocument();
    expect(screen.getByText(/Schváliť build/)).toBeInTheDocument();
  });

  it("gate_g awaiting → the verdict decision", () => {
    render(<WhosTurnBoard state={mkState("gate_g", "auditor", "awaiting_director")} availableActions={["verdict", "ask"]} />);
    expect(screen.getByText("Verdikt auditu (PASS / FAIL)")).toBeInTheDocument();
  });

  it("release awaiting → the UAT-accept decision", () => {
    render(<WhosTurnBoard state={mkState("release", "director", "awaiting_director")} availableActions={["uat_accept", "ask"]} />);
    expect(screen.getByText("Akceptovať verziu (UAT)")).toBeInTheDocument();
  });

  it("gate_e awaiting → relay breadcrumb (gate_e is relayed) + ratify decision", () => {
    render(
      <WhosTurnBoard
        state={mkState("gate_e", "customer", "awaiting_director")}
        availableActions={["approve", "fix", "leave", "end_gate_e", "ask"]}
      />,
    );
    expect(screen.getByText("cez Koordinátora")).toBeInTheDocument(); // gate_e IS relayed
    expect(screen.getByText("Schváliť alebo vrátiť")).toBeInTheDocument();
  });

  it("blocked (agent question) → 'Odpovedať / vrátiť'", () => {
    render(<WhosTurnBoard state={mkState("build", "implementer", "blocked")} availableActions={["answer", "return", "ask"]} />);
    expect(screen.getByText("Odpovedať / vrátiť")).toBeInTheDocument();
  });

  it("agent_working → '{actor} pracuje', no decision / relay (honest actor, not a stale stage label)", () => {
    render(<WhosTurnBoard state={mkState("build", "implementer", "agent_working")} availableActions={["pause"]} />);
    expect(screen.getByText("Programátor pracuje")).toBeInTheDocument();
    expect(screen.queryByText("cez Koordinátora")).not.toBeInTheDocument();
  });

  it("paused → 'Build pozastavený' + the resume decision", () => {
    render(<WhosTurnBoard state={mkState("build", "implementer", "paused")} availableActions={["continue_build", "end_build"]} />);
    expect(screen.getByText("Build pozastavený")).toBeInTheDocument();
    expect(screen.getByText("Pokračovať alebo ukončiť build")).toBeInTheDocument();
  });

  it("surfaces the Coordinator's proposed action by its effect", () => {
    const proposal: CoordinatorDirective = {
      triage_class: "programmer_guidance",
      proposed_action: "coordinator_move_baseline",
      rationale: "merged commit",
      confidence: 0.9,
    };
    render(
      <WhosTurnBoard
        state={mkState("build", "implementer", "awaiting_director")}
        availableActions={["apply_coordinator_recommendation", "return", "ask"]}
        coordinatorProposal={proposal}
      />,
    );
    expect(screen.getByText(/Návrh Koordinátora:.*posunúť baseline/)).toBeInTheDocument();
  });
});
