/**
 * PipelineRail agent chips — unified status colours (CR-NS-028).
 * working = blue (sky), awaiting = amber, blocked = red, idle = neutral — never emerald-for-working.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineRail from "@/components/cockpit/PipelineRail";
import type { PipelineState, PipelineStatus } from "@/services/api/pipeline";

function mkState(status: PipelineStatus, overrides: Partial<PipelineState> = {}): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: "build",
    current_actor: "implementer",
    status,
    next_action: "",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
    ...overrides,
  };
}

describe("PipelineRail — unified chip colours (CR-NS-028)", () => {
  it("the active agent's working chip = blue (not emerald)", () => {
    render(<PipelineRail state={mkState("agent_working")} activeAgent="implementer" />);
    const chip = screen.getByText("working");
    // CR-NS-067c: TONE_TEXT is now theme-aware (text-X-600 dark:text-X-400) — assert the light base.
    expect(chip).toHaveClass("text-sky-600");
    expect(chip).not.toHaveClass("text-emerald-600"); // no emerald-for-working
  });

  it("awaiting chip = amber, blocked chip = red", () => {
    const { rerender } = render(<PipelineRail state={mkState("awaiting_director")} activeAgent="implementer" />);
    expect(screen.getByText("awaiting")).toHaveClass("text-amber-600");
    rerender(<PipelineRail state={mkState("blocked")} activeAgent="implementer" />);
    expect(screen.getByText("blocked")).toHaveClass("text-red-600");
  });
});

describe("PipelineRail — fast_fix short stage path (CR-NS-095)", () => {
  it("renders ONLY the short lane stages for a fast_fix flow, not the full waterfall", () => {
    render(<PipelineRail state={mkState("agent_working", { flow_type: "fast_fix", current_stage: "build" })} />);

    // Short lane present: kickoff → build → release → done.
    expect(screen.getByText("Príprava")).toBeInTheDocument();
    expect(screen.getByText("Programovanie")).toBeInTheDocument();
    expect(screen.getByText("Vydanie")).toBeInTheDocument();
    expect(screen.getByText("Hotovo")).toBeInTheDocument();

    // Full-waterfall-only stages are skipped (absent from the rail).
    expect(screen.queryByText("Rozsah")).not.toBeInTheDocument(); // gate_a
    expect(screen.queryByText("Kontrola zákazníkom")).not.toBeInTheDocument(); // gate_e
    expect(screen.queryByText("Plán úloh")).not.toBeInTheDocument(); // task_plan
    expect(screen.queryByText("Audit")).not.toBeInTheDocument(); // gate_g

    // Distinct lane badge.
    expect(screen.getByText("Rýchla oprava")).toBeInTheDocument();
  });

  it("a new_version flow keeps the full rail and shows no fast-fix badge", () => {
    render(<PipelineRail state={mkState("agent_working", { flow_type: "new_version", current_stage: "build" })} />);
    expect(screen.getByText("Rozsah")).toBeInTheDocument(); // gate_a present
    expect(screen.getByText("Audit")).toBeInTheDocument(); // gate_g present
    expect(screen.queryByText("Rýchla oprava")).not.toBeInTheDocument();
  });
});

describe("PipelineRail — finished pipeline ticks the terminal stage (CR-NS-099)", () => {
  it("a done fast_fix run shows 'Hotovo' as completed (✓), not the in-progress '>'", () => {
    render(<PipelineRail state={mkState("done", { flow_type: "fast_fix", current_stage: "done" })} />);
    const hotovo = screen.getByText("Hotovo").closest("li");
    expect(hotovo).toHaveTextContent("✓"); // completed marker
    expect(hotovo).not.toHaveTextContent(">"); // NOT the current/in-progress marker
  });

  it("a done new_version run also ticks its terminal 'Hotovo'", () => {
    render(<PipelineRail state={mkState("done", { current_stage: "done" })} />);
    const hotovo = screen.getByText("Hotovo").closest("li");
    expect(hotovo).toHaveTextContent("✓");
    expect(hotovo).not.toHaveTextContent(">");
  });
});
