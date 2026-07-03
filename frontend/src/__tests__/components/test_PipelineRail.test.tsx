/**
 * Vývoj horizontal 4-phase bar (CR-V2-021) — the chips ARE the tabs. The bar shows ✓ done / ● current /
 * ○ pending markers, highlights the VIEWED tab (which may differ from the build position ●), and each chip
 * is clickable. The WhosUp who's-up status names the working agent / čaká na Manažéra.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineRail, { WhosUp } from "@/components/cockpit/PipelineRail";
import type { PipelineState, PipelineStatus } from "@/services/api/pipeline";
import type { BuildPhase } from "@/components/cockpit/labels";

function mkState(status: PipelineStatus, overrides: Partial<PipelineState> = {}): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: "programovanie",
    current_actor: "ai_agent",
    status,
    next_action: "",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
    ...overrides,
  };
}

describe("PipelineRail — 4-phase bar (chips = tabs)", () => {
  it("renders the four phase chips as tabs (Hotovo is not a tab)", () => {
    render(<PipelineRail state={mkState("agent_working")} viewedPhase="programovanie" onSelectPhase={() => {}} />);
    for (const label of ["Príprava", "Návrh", "Programovanie", "Verifikácia"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("Hotovo")).not.toBeInTheDocument();
  });

  it("marks phases ✓ done / ● current / ○ pending by the build position", () => {
    // Build is at Programovanie → Príprava+Návrh done, Programovanie current, Verifikácia pending.
    const { container } = render(
      <PipelineRail state={mkState("agent_working")} viewedPhase="programovanie" onSelectPhase={() => {}} />,
    );
    const chips = (label: string) => screen.getByText(label).closest("button")!;
    expect(chips("Príprava")).toHaveTextContent("✓");
    expect(chips("Programovanie")).toHaveTextContent("●");
    expect(chips("Verifikácia")).toHaveTextContent("○");
    expect(container).toBeTruthy();
  });

  it("a done build ticks the terminal Verifikácia as ✓", () => {
    render(<PipelineRail state={mkState("done", { current_stage: "done" })} viewedPhase="verifikacia" onSelectPhase={() => {}} />);
    expect(screen.getByText("Verifikácia").closest("button")).toHaveTextContent("✓");
  });

  it("highlights the VIEWED tab even when it differs from the build position", () => {
    // Build runs in Programovanie (●) but the Manažér is viewing Návrh.
    render(<PipelineRail state={mkState("agent_working")} viewedPhase="navrh" onSelectPhase={() => {}} />);
    expect(screen.getByText("Návrh").closest("button")).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("Programovanie").closest("button")).not.toHaveAttribute("aria-current");
  });

  it("a chip click selects that phase tab", () => {
    const onSelect = vi.fn();
    render(<PipelineRail state={mkState("agent_working")} viewedPhase="programovanie" onSelectPhase={onSelect} />);
    fireEvent.click(screen.getByText("Návrh"));
    expect(onSelect).toHaveBeenCalledWith<[BuildPhase]>("navrh");
  });
});

describe("WhosUp — who's-up status", () => {
  it("names the working agent + the Programovanie task in focus", () => {
    render(
      <WhosUp
        state={mkState("agent_working")}
        activeAgent="ai_agent"
        currentTask={{ number: 3, title: "Faktúry API" }}
      />,
    );
    expect(screen.getByText(/AI Agent pracuje/)).toBeInTheDocument();
    expect(screen.getByText(/#3: Faktúry API/)).toBeInTheDocument();
  });

  it("shows 'čaká na Manažéra' at a settled stop", () => {
    render(<WhosUp state={mkState("awaiting_manazer")} />);
    expect(screen.getByText("čaká na Manažéra")).toBeInTheDocument();
  });

  it("renders nothing at done", () => {
    const { container } = render(<WhosUp state={mkState("done", { current_stage: "done" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("surfaces a stale session indicator", () => {
    render(
      <WhosUp
        state={mkState("agent_working")}
        activeAgent="ai_agent"
        agentSessions={[
          { role: "ai_agent", status: "active" },
          { role: "auditor", status: "stale" },
        ]}
      />,
    );
    expect(screen.getByText(/session nečinná/)).toBeInTheDocument();
  });

  // CR-V2-056: the board computes verified LIVE; 'sha_drift' means the recorded PASS is stale (HEAD moved
  // past the verified commit) — the screen must reflect reality, not a frozen green.
  it("surfaces a stale-PASS warning when the verified state drifted (HEAD moved)", () => {
    render(<WhosUp state={mkState("awaiting_manazer")} verifiedProvenance="sha_drift" />);
    expect(screen.getByText(/overenie zastarané/i)).toBeInTheDocument();
  });

  it("surfaces the drift warning even on a done build (which otherwise renders nothing)", () => {
    render(<WhosUp state={mkState("done", { current_stage: "done" })} verifiedProvenance="sha_drift" />);
    expect(screen.getByText(/overenie zastarané/i)).toBeInTheDocument();
  });

  it("shows no drift warning when verified matches the current HEAD", () => {
    render(<WhosUp state={mkState("awaiting_manazer")} verifiedProvenance="sha_match" />);
    expect(screen.queryByText(/overenie zastarané/i)).not.toBeInTheDocument();
  });

  // CR-V2-057: "Over znova" is the remedy for a drifted PASS — re-run Verifikácia against the current code.
  // It sits next to the warning and fires the overit_znovu action.
  it("offers an 'Over znova' button next to the drift warning and fires onReverify", () => {
    const onReverify = vi.fn();
    render(
      <WhosUp state={mkState("awaiting_manazer")} verifiedProvenance="sha_drift" onReverify={onReverify} />,
    );
    const btn = screen.getByRole("button", { name: /over znova/i });
    fireEvent.click(btn);
    expect(onReverify).toHaveBeenCalledOnce();
  });

  it("shows no 'Over znova' button when there is no drift", () => {
    render(<WhosUp state={mkState("awaiting_manazer")} verifiedProvenance="sha_match" onReverify={() => {}} />);
    expect(screen.queryByRole("button", { name: /over znova/i })).not.toBeInTheDocument();
  });

  it("disables the 'Over znova' button while a re-verify is in flight", () => {
    render(
      <WhosUp
        state={mkState("done", { current_stage: "done" })}
        verifiedProvenance="sha_drift"
        onReverify={() => {}}
        reverifyInFlight
      />,
    );
    expect(screen.getByRole("button", { name: /overujem/i })).toBeDisabled();
  });
});
