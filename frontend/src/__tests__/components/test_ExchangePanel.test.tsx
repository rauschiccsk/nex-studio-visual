/**
 * ExchangePanel banner (CR-NS-018) — composed from Slovak labels, never the raw
 * backend next_action (which embeds machine tokens like 'coordinator').
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ExchangePanel from "@/components/cockpit/ExchangePanel";
import type { PipelineBoard, PipelineStage, PipelineActor, PipelineStatus } from "@/services/api/pipeline";

function mkBoard(stage: PipelineStage, actor: PipelineActor, status: PipelineStatus): PipelineBoard {
  return {
    state: {
      id: "11111111-1111-1111-1111-111111111111",
      version_id: "22222222-2222-2222-2222-222222222222",
      flow_type: "new_version",
      current_stage: stage,
      current_actor: actor,
      status,
      // deliberately machine-token-laden — must NOT be rendered verbatim
      next_action: "Agent 'coordinator' pracuje na fáze 'gate_a'.",
      is_regate: false,
      iteration: 0,
      created_at: "2026-06-04T00:00:00Z",
      updated_at: "2026-06-04T00:00:00Z",
    },
    recent_messages: [],
  };
}

describe("ExchangePanel banner", () => {
  it("agent_working → composed Slovak banner, no raw machine tokens", () => {
    render(<ExchangePanel board={mkBoard("gate_a", "designer", "agent_working")} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Návrhár pracuje na fáze Rozsah")).toBeInTheDocument();
    expect(screen.queryByText(/coordinator/)).not.toBeInTheDocument();
    expect(screen.queryByText(/gate_a/)).not.toBeInTheDocument();
  });

  it("awaiting_director → 'Na rade: Director — posúď fázu {stage}'", () => {
    render(<ExchangePanel board={mkBoard("gate_g", "auditor", "awaiting_director")} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Na rade: Director — posúď fázu Audit")).toBeInTheDocument();
  });

  it("blocked → 'odpovedz {role}-ovi' (question stays in the thread)", () => {
    render(<ExchangePanel board={mkBoard("gate_a", "designer", "blocked")} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Na rade: Director — odpovedz Návrhár-ovi")).toBeInTheDocument();
  });
});
