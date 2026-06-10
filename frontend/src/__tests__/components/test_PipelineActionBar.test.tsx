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
import type { PipelineState, PipelineStage, PipelineStatus, PipelineActionName } from "@/services/api/pipeline";

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

  it("while build is agent_working shows Pauza (no ratify buttons)", () => {
    // CR-NS-027: Pauza is build-only now. At build/agent_working it shows; ratify/start never leak.
    render(<PipelineActionBar state={mkState("build", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Pauza")).toBeInTheDocument();
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
    expect(screen.queryByText("Spustiť")).not.toBeInTheDocument();
  });

  it("while a gate is agent_working shows no controls (Pauza is build-only)", () => {
    render(<PipelineActionBar state={mkState("kickoff", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.queryByText("Pauza")).not.toBeInTheDocument();
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

describe("PipelineActionBar — Gate E per-question (revised §2)", () => {
  const gateE = (props: { gateEMode?: "question" | "boundary"; gateEGap?: boolean; onAction?: () => void }) => (
    <PipelineActionBar
      state={mkState("gate_e", "awaiting_director")}
      inFlight={false}
      gateEMode={props.gateEMode}
      gateEGap={props.gateEGap}
      onAction={props.onAction ?? vi.fn()}
    />
  );

  it("Branch A (no gap) shows Schváliť odpoveď → approve, no Branch-B buttons", () => {
    const onAction = vi.fn();
    render(gateE({ gateEMode: "question", onAction }));
    const approve = screen.getByText("Schváliť odpoveď");
    expect(screen.queryByText(COORD)).not.toBeInTheDocument();
    expect(screen.queryByText("Ponechať")).not.toBeInTheDocument();
    approve.click();
    expect(onAction).toHaveBeenCalledWith("approve");
  });

  it("Branch B (gap) shows Schváliť návrh Koordinátora (fix) / Ponechať (leave)", () => {
    const onAction = vi.fn();
    render(gateE({ gateEMode: "question", gateEGap: true, onAction }));
    expect(screen.queryByText("Schváliť odpoveď")).not.toBeInTheDocument();
    screen.getByText(COORD).click(); // "Schváliť návrh Koordinátora" → fix
    expect(onAction).toHaveBeenCalledWith("fix");
    screen.getByText("Ponechať").click();
    expect(onAction).toHaveBeenCalledWith("leave");
  });

  it("the consult button at gate_e reads 'Konzultovať s Koordinátorom' (not 'Otázka')", () => {
    render(gateE({ gateEMode: "question" }));
    expect(screen.getByText("Konzultovať s Koordinátorom")).toBeInTheDocument();
    expect(screen.queryByText("Otázka")).not.toBeInTheDocument();
  });

  it("per-question stop shows no topic-boundary buttons", () => {
    render(gateE({ gateEMode: "question" }));
    expect(screen.queryByText("Schváliť okruh a pokračovať")).not.toBeInTheDocument();
    expect(screen.queryByText("Ukončiť Gate E")).not.toBeInTheDocument();
    expect(screen.queryByText(COORD)).not.toBeInTheDocument();
  });
});

describe("PipelineActionBar — Gate E topic boundary (Phase 3)", () => {
  it("topic boundary shows continue + Ukončiť Gate E, not the generic ratify buttons", () => {
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateEMode="boundary"
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť okruh a pokračovať")).toBeInTheDocument();
    expect(screen.getByText("Ukončiť Gate E")).toBeInTheDocument();
    expect(screen.queryByText(APPROVE)).not.toBeInTheDocument();
    expect(screen.queryByText(COORD)).not.toBeInTheDocument();
    expect(screen.queryByText("Finálne schválenie → Plán úloh")).not.toBeInTheDocument();
  });

  it("topic-boundary approve fires the plain approve (continue topic)", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateEMode="boundary"
        onAction={onAction}
      />,
    );
    screen.getByText("Schváliť okruh a pokračovať").click();
    expect(onAction).toHaveBeenCalledWith("approve");
  });

  it("Ukončiť Gate E is disabled while findings are open", () => {
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateEMode="boundary"
        gateEOpenFindings={2}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Ukončiť Gate E").closest("button")).toBeDisabled();
  });

  it("Ukončiť Gate E fires end_gate_e when no open findings", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("gate_e", "awaiting_director")}
        inFlight={false}
        gateEMode="boundary"
        onAction={onAction}
      />,
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
        gateEMode="boundary"
        gateECoverageComplete
        onAction={onAction}
      />,
    );
    const final = screen.getByText("Finálne schválenie → Plán úloh");
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
        gateEMode="boundary"
        gateECoverageComplete
        gateEOpenFindings={1}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Finálne schválenie → Plán úloh").closest("button")).toBeDisabled();
  });
});

describe("PipelineActionBar — build controls (CR-NS-020 CR-5)", () => {
  it("offers the full build control set at build/awaiting_director", () => {
    render(<PipelineActionBar state={mkState("build", "awaiting_director")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Schváliť build → Audit")).toBeInTheDocument();
    expect(screen.getByText("Pokračovať v builde")).toBeInTheDocument();
    expect(screen.getByText("Vrátiť úlohu")).toBeInTheDocument();
    expect(screen.getByText("Ukončiť build (zvyšok do auditu)")).toBeInTheDocument();
  });

  it("fires continue_build / approve / end_build with no payload", () => {
    const onAction = vi.fn();
    render(<PipelineActionBar state={mkState("build", "awaiting_director")} inFlight={false} onAction={onAction} />);
    screen.getByText("Pokračovať v builde").click();
    expect(onAction).toHaveBeenCalledWith("continue_build");
    screen.getByText("Schváliť build → Audit").click();
    expect(onAction).toHaveBeenCalledWith("approve");
    screen.getByText("Ukončiť build (zvyšok do auditu)").click();
    expect(onAction).toHaveBeenCalledWith("end_build");
  });

  it("hides the build controls while build is agent_working", () => {
    render(<PipelineActionBar state={mkState("build", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.queryByText("Pokračovať v builde")).not.toBeInTheDocument();
    expect(screen.queryByText("Schváliť build → Audit")).not.toBeInTheDocument();
  });
});

describe("PipelineActionBar — task_plan ratify gate (CR-NS-023)", () => {
  it("offers Schváliť podľa Návrhára + Vrátiť at task_plan/awaiting_director", () => {
    const onAction = vi.fn();
    render(<PipelineActionBar state={mkState("task_plan", "awaiting_director")} inFlight={false} onAction={onAction} />);
    expect(screen.getByText("Schváliť podľa Návrhára")).toBeInTheDocument();
    expect(screen.getByText("Vrátiť")).toBeInTheDocument();
    screen.getByText("Schváliť podľa Návrhára").click();
    expect(onAction).toHaveBeenCalledWith("approve");
  });

  it("does not show the ratify gate while task_plan is agent_working", () => {
    render(<PipelineActionBar state={mkState("task_plan", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.queryByText("Schváliť podľa Návrhára")).not.toBeInTheDocument();
  });
});

describe("PipelineActionBar — pause is build-only (CR-NS-027)", () => {
  it("offers Pauza at build/agent_working and fires onAction('pause')", () => {
    const onAction = vi.fn();
    render(<PipelineActionBar state={mkState("build", "agent_working")} inFlight={false} onAction={onAction} />);
    expect(screen.getByText("Pauza")).toBeInTheDocument();
    screen.getByText("Pauza").click();
    expect(onAction).toHaveBeenCalledWith("pause");
  });

  it("does not offer Pauza at a gate (no cooperative boundary there)", () => {
    render(<PipelineActionBar state={mkState("gate_a", "agent_working")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.queryByText("Pauza")).not.toBeInTheDocument();
  });
});

describe("PipelineActionBar — backend-authoritative available_actions (CR-NS-030)", () => {
  it("at build/blocked hides the no-op approve but keeps Odpoveď when the backend says so", () => {
    render(
      <PipelineActionBar
        state={mkState("build", "blocked")}
        availableActions={["answer", "return", "ask", "continue_build", "end_build"]}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    // approve is NOT in available_actions at a build-blocked task → the no-op button is gone
    expect(screen.queryByText("Schváliť podľa Návrhára")).not.toBeInTheDocument();
    // a programmer-question still offers Odpoveď (answer ∈ available_actions)
    expect(screen.getByText("Odpoveď")).toBeInTheDocument();
  });

  it("renders only the backend-allowed buttons (drops Vrátiť when not allowed)", () => {
    render(
      <PipelineActionBar
        state={mkState("gate_a", "awaiting_director")}
        availableActions={["approve", "ask"]} // return deliberately omitted
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť podľa Návrhára")).toBeInTheDocument(); // approve allowed
    expect(screen.queryByText("Vrátiť")).not.toBeInTheDocument(); // return NOT allowed → hidden
  });

  it("falls back to the FE's own logic when available_actions is absent (backward compat)", () => {
    // no availableActions prop → allowed() returns true → the legacy question-block still shows approve
    render(<PipelineActionBar state={mkState("build", "blocked")} inFlight={false} onAction={vi.fn()} />);
    expect(screen.getByText("Schváliť podľa Návrhára")).toBeInTheDocument();
  });
});

describe("PipelineActionBar — build readiness + paused (CR-NS-030 fold)", () => {
  const buildActions: PipelineActionName[] = ["approve", "continue_build", "return", "end_build", "ask"];

  it("disables 'Schváliť build → Audit' while tasks remain, enables it when all done", () => {
    const { rerender } = render(
      <PipelineActionBar
        state={mkState("build", "awaiting_director")}
        availableActions={buildActions}
        allTasksDone={false}
        buildOpenFindings={0}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť build → Audit").closest("button")).toBeDisabled();
    rerender(
      <PipelineActionBar
        state={mkState("build", "awaiting_director")}
        availableActions={buildActions}
        allTasksDone={true}
        buildOpenFindings={0}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť build → Audit").closest("button")).not.toBeDisabled();
  });

  it("disables 'Ukončiť build' while open findings remain", () => {
    render(
      <PipelineActionBar
        state={mkState("build", "awaiting_director")}
        availableActions={buildActions}
        allTasksDone={true}
        buildOpenFindings={2}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Ukončiť build (zvyšok do auditu)").closest("button")).toBeDisabled();
  });

  it("offers the resume pair (continue_build + end_build) when the build is paused", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("build", "paused")}
        availableActions={["continue_build", "end_build"]}
        inFlight={false}
        onAction={onAction}
      />,
    );
    expect(screen.getByText("Pokračovať v builde")).toBeInTheDocument();
    expect(screen.getByText("Ukončiť build (zvyšok do auditu)")).toBeInTheDocument();
    screen.getByText("Pokračovať v builde").click();
    expect(onAction).toHaveBeenCalledWith("continue_build");
  });
});

describe("PipelineActionBar — accept_merged (WS-B2, CR-NS-031)", () => {
  const buildActions: PipelineActionName[] = [
    "approve",
    "continue_build",
    "return",
    "end_build",
    "accept_merged",
    "ask",
  ];

  it("offers 'Uznať spoločný commit' at a build HALT (open findings) and fires accept_merged", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("build", "awaiting_director")}
        availableActions={buildActions}
        allTasksDone={true}
        buildOpenFindings={1}
        inFlight={false}
        onAction={onAction}
      />,
    );
    const btn = screen.getByText("Uznať spoločný commit");
    expect(btn).toBeInTheDocument();
    btn.click();
    expect(onAction).toHaveBeenCalledWith("accept_merged");
  });

  it("hides 'Uznať spoločný commit' on a clean build (no open findings)", () => {
    render(
      <PipelineActionBar
        state={mkState("build", "awaiting_director")}
        availableActions={buildActions}
        allTasksDone={true}
        buildOpenFindings={0}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.queryByText("Uznať spoločný commit")).not.toBeInTheDocument();
  });

  it("hides 'Uznať spoločný commit' when the backend omits accept_merged (allowed() gate)", () => {
    render(
      <PipelineActionBar
        state={mkState("build", "awaiting_director")}
        availableActions={["approve", "continue_build", "return", "end_build", "ask"]} // accept_merged omitted
        allTasksDone={true}
        buildOpenFindings={1} // would show on findings alone — but the backend doesn't allow it
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.queryByText("Uznať spoločný commit")).not.toBeInTheDocument();
  });
});
