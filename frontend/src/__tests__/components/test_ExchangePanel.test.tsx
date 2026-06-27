/**
 * ExchangePanel — per-phase content panel for the Vývoj board (CR-V2-021). Renders the VIEWED phase's
 * durable artifact (from recent_messages' gate_report / verdict report payload), shows the live status
 * banner only when the viewed tab IS the build position, and keeps a finished phase viewable after the
 * build advances (no vanish).
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ExchangePanel from "@/components/cockpit/ExchangePanel";
import type { PipelineBoard, PipelineMessage, PipelineStage, PipelineActor, PipelineStatus } from "@/services/api/pipeline";

function mkArtifact(stage: PipelineStage, report: string): PipelineMessage {
  return {
    id: `m-${stage}`,
    version_id: "22222222-2222-2222-2222-222222222222",
    stage,
    author: stage === "verifikacia" ? "auditor" : "ai_agent",
    recipient: "manazer",
    kind: stage === "verifikacia" ? "verdict" : "gate_report",
    content: `${stage} súhrn`,
    status: "delivered",
    payload: { report },
    created_at: "2026-06-27T00:00:00Z",
    seq: 1,
  };
}

function mkBoard(
  stage: PipelineStage,
  actor: PipelineActor,
  status: PipelineStatus,
  messages: PipelineMessage[] = [],
): PipelineBoard {
  return {
    state: {
      id: "11111111-1111-1111-1111-111111111111",
      version_id: "22222222-2222-2222-2222-222222222222",
      flow_type: "new_version",
      current_stage: stage,
      current_actor: actor,
      status,
      next_action: "Agent 'ai_agent' pracuje na fáze 'navrh'.", // machine-token-laden — must NOT render verbatim
      is_regate: false,
      iteration: 0,
      created_at: "2026-06-27T00:00:00Z",
      updated_at: "2026-06-27T00:00:00Z",
    },
    recent_messages: messages,
  };
}

describe("ExchangePanel — phase artifact", () => {
  it("renders the viewed phase's artifact (Špecifikácia in Príprava)", () => {
    const board = mkBoard("priprava", "ai_agent", "awaiting_manazer", [
      mkArtifact("priprava", "# Špecifikácia\nDPH a IBAN povinné"),
    ]);
    render(<ExchangePanel board={board} viewedPhase="priprava" activity={[]} />);
    expect(screen.getByText("DPH a IBAN povinné")).toBeInTheDocument();
  });

  it("a finished phase stays viewable after the build advances (no vanish)", () => {
    // Build is in Programovanie; the Manažér reviews the finished Návrh tab.
    const board = mkBoard("programovanie", "ai_agent", "agent_working", [
      mkArtifact("navrh", "## Dátový model\nFaktúra → Položky"),
      mkArtifact("programovanie", "task #1 hotová"),
    ]);
    render(<ExchangePanel board={board} viewedPhase="navrh" activity={[]} />);
    expect(screen.getByText("Faktúra → Položky")).toBeInTheDocument();
  });

  it("shows a placeholder when the viewed phase has no artifact yet", () => {
    render(<ExchangePanel board={mkBoard("priprava", "ai_agent", "agent_working")} viewedPhase="verifikacia" activity={[]} />);
    expect(screen.getByText(/Auditor ešte nevydal verdikt/)).toBeInTheDocument();
  });

  it("shows the live status banner only when viewing the build position (composed Slovak, no raw tokens)", () => {
    const board = mkBoard("navrh", "ai_agent", "agent_working");
    const { rerender } = render(<ExchangePanel board={board} viewedPhase="navrh" activity={[]} />);
    expect(screen.getByText("Prebieha fáza Návrh")).toBeInTheDocument();
    expect(screen.queryByText(/ai_agent/)).not.toBeInTheDocument();
    expect(screen.queryByText(/navrh'/)).not.toBeInTheDocument();
    // Viewing a NON-live tab → no live banner.
    rerender(<ExchangePanel board={board} viewedPhase="priprava" activity={[]} />);
    expect(screen.queryByText("Prebieha fáza Návrh")).not.toBeInTheDocument();
  });

  it("Programovanie renders the split view (task-plan slot on the right)", () => {
    const board = mkBoard("programovanie", "ai_agent", "agent_working", [mkArtifact("programovanie", "log")]);
    render(
      <ExchangePanel
        board={board}
        viewedPhase="programovanie"
        activity={[]}
        taskPlanSlot={<div data-testid="task-plan-slot">plán</div>}
      />,
    );
    expect(screen.getByTestId("task-plan-slot")).toBeInTheDocument();
  });
});
