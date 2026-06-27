/**
 * Schvaľovacie body — PipelineActionBar (CR-V2-021). Which buttons appear is backend-authoritative: the bar
 * renders ONLY the board's dial-governed ``availableActions`` (it can never offer a no-op verb). The text
 * actions (uprav / ask / answer) open an inline composer; the Programovanie sign-off disables when a task
 * is unfinished.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineActionBar from "@/components/cockpit/PipelineActionBar";
import type { PipelineState, PipelineStage, PipelineStatus, PipelineActionName } from "@/services/api/pipeline";

function mkState(stage: PipelineStage, status: PipelineStatus): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: stage,
    current_actor: "ai_agent",
    status,
    next_action: "x",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-27T00:00:00Z",
    updated_at: "2026-06-27T00:00:00Z",
  };
}

describe("PipelineActionBar — schvaľovacie body (v2)", () => {
  it("renders ONLY the backend-offered actions", () => {
    render(
      <PipelineActionBar
        state={mkState("priprava", "awaiting_manazer")}
        availableActions={["approve_spec", "uprav", "ask"]}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť špecifikáciu")).toBeInTheDocument();
    expect(screen.getByText("Uprav")).toBeInTheDocument();
    expect(screen.getByText("Spýtať sa")).toBeInTheDocument();
    // Not offered → not rendered.
    expect(screen.queryByText(/Schváliť →/)).not.toBeInTheDocument();
    expect(screen.queryByText("Pokračovať")).not.toBeInTheDocument();
  });

  it("approve_spec dispatches the always-mandatory spec approval", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("priprava", "awaiting_manazer")}
        availableActions={["approve_spec"]}
        inFlight={false}
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByText("Schváliť špecifikáciu"));
    expect(onAction).toHaveBeenCalledWith<[PipelineActionName]>("approve_spec");
  });

  it("schvalit at Návrh shows the next-phase consequence", () => {
    render(
      <PipelineActionBar
        state={mkState("navrh", "awaiting_manazer")}
        availableActions={["schvalit", "uprav"]}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť → Programovanie")).toBeInTheDocument();
  });

  it("disables the Programovanie schvalit while a task is unfinished", () => {
    render(
      <PipelineActionBar
        state={mkState("programovanie", "awaiting_manazer")}
        availableActions={["schvalit"]}
        allTasksDone={false}
        inFlight={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Schváliť → Verifikácia").closest("button")).toBeDisabled();
  });

  it("Verifikácia offers PASS + FAIL verdict buttons", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("verifikacia", "awaiting_manazer")}
        availableActions={["verdict", "schvalit"]}
        inFlight={false}
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByText("Verdikt PASS"));
    expect(onAction).toHaveBeenCalledWith("verdict", { verdict: "PASS" });
    fireEvent.click(screen.getByText("Verdikt FAIL"));
    expect(onAction).toHaveBeenCalledWith("verdict", { verdict: "FAIL" });
  });

  it("uprav opens a composer and dispatches the comment", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("navrh", "awaiting_manazer")}
        availableActions={["uprav"]}
        inFlight={false}
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByText("Uprav"));
    const box = screen.getByPlaceholderText("Napíš správu…");
    fireEvent.change(box, { target: { value: "pridaj DPH pole" } });
    fireEvent.click(screen.getByText("Odoslať"));
    expect(onAction).toHaveBeenCalledWith("uprav", { comment: "pridaj DPH pole" });
  });

  it("answer opens a composer and dispatches the text on a blocked question", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("programovanie", "blocked")}
        availableActions={["answer", "uprav"]}
        inFlight={false}
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByText("Odpovedať"));
    fireEvent.change(screen.getByPlaceholderText("Napíš správu…"), { target: { value: "áno, pokračuj" } });
    fireEvent.click(screen.getByText("Odoslať"));
    expect(onAction).toHaveBeenCalledWith("answer", { text: "áno, pokračuj" });
  });

  it("pokracovat resumes a paused Programovanie loop", () => {
    const onAction = vi.fn();
    render(
      <PipelineActionBar
        state={mkState("programovanie", "paused")}
        availableActions={["pokracovat", "uprav"]}
        inFlight={false}
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByText("Pokračovať"));
    expect(onAction).toHaveBeenCalledWith<[PipelineActionName]>("pokracovat");
  });

  it("renders nothing without state", () => {
    const { container } = render(<PipelineActionBar state={null} inFlight={false} onAction={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
