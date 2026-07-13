/**
 * Component tests for PhaseBar — the read-only phase strip at the top of the Riadiace centrum.
 *
 * Two derivations, pinned here (STEP 5, docs/architecture/step5-kontrola-design.md MAJOR):
 *   - LEGACY (phase automaton): the four v2 phases Príprava › Návrh › Programovanie › Verifikácia, marked
 *     from `state.current_stage`. A null board renders every phase neutral (never crash). Byte-identical to
 *     the pre-STEP-5 bar (no redesign labels leak in).
 *   - CONVERSATION (spine build, `state.mode === 'conversation'`): the redesign phases Špecifikácia → Plán →
 *     Programovanie → Kontrola, with the current one DERIVED FROM BOARD SIGNALS (available_actions /
 *     recent-message payload flags / status), NOT the stage index (a spine build stays on 'priprava').
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PhaseBar from "@/components/riadiace/PhaseBar";
import type {
  PipelineActionName,
  PipelineBoard,
  PipelineMessage,
  PipelineState,
} from "@/services/api/pipeline";

function mkState(over: Partial<PipelineState> = {}): PipelineState {
  return {
    id: "s1",
    version_id: "v1",
    flow_type: "new_version",
    current_stage: "priprava",
    current_actor: "ai_agent",
    status: "agent_working",
    next_action: "",
    is_regate: false,
    iteration: 0,
    block_reason: null,
    created_at: "2026-07-05T00:00:00Z",
    updated_at: "2026-07-05T00:00:00Z",
    ...over,
  };
}

function mkBoard(over: Partial<PipelineBoard> = {}): PipelineBoard {
  return { state: mkState(), recent_messages: [], ...over };
}

function mkMsg(payload: Record<string, unknown>): PipelineMessage {
  return {
    id: "m1",
    version_id: "v1",
    stage: "priprava",
    author: "ai_agent",
    recipient: "manazer",
    kind: "gate_report",
    content: "",
    status: "delivered",
    payload,
    created_at: "2026-07-05T00:00:00Z",
    seq: 1,
  };
}

// A conversation-mode board with the given signals baked in.
function convBoard(over: {
  actions?: PipelineActionName[];
  messages?: PipelineMessage[];
  specApproved?: boolean;
  status?: PipelineState["status"];
  currentStage?: PipelineState["current_stage"];
}): PipelineBoard {
  return mkBoard({
    state: mkState({ mode: "conversation", status: over.status ?? "agent_working", current_stage: over.currentStage ?? "priprava" }),
    available_actions: over.actions,
    recent_messages: over.messages ?? [],
    spec_approved: over.specApproved,
  });
}

// The current phase's label span carries `font-semibold`; every other phase is not bold.
function isCurrent(text: string): boolean {
  return screen.getByText(text).className.includes("font-semibold");
}

const LEGACY_LABELS = ["Príprava", "Návrh", "Programovanie", "Verifikácia"];
const CONV_LABELS = ["Špecifikácia", "Plán", "Vizuál", "Programovanie", "Kontrola"];

describe("PhaseBar — legacy phase-automaton strip (byte-identical)", () => {
  it("renders the four legacy phases and marks current_stage as current (no redesign labels leak in)", () => {
    render(<PhaseBar board={mkBoard({ state: mkState({ current_stage: "navrh" }) })} />);

    for (const label of LEGACY_LABELS) expect(screen.getByText(label)).toBeInTheDocument();
    // Redesign-only labels never appear on the legacy strip.
    expect(screen.queryByText("Špecifikácia")).not.toBeInTheDocument();
    expect(screen.queryByText("Kontrola")).not.toBeInTheDocument();

    // current_stage='navrh' → Návrh is current; Príprava (before) is not bold.
    expect(isCurrent("Návrh")).toBe(true);
    expect(isCurrent("Príprava")).toBe(false);
  });

  it("tolerates a null board without crashing (every phase neutral)", () => {
    render(<PhaseBar board={null} />);
    for (const label of LEGACY_LABELS) expect(screen.getByText(label)).toBeInTheDocument();
    // Nothing is current when there is no state.
    for (const label of LEGACY_LABELS) expect(isCurrent(label)).toBe(false);
  });

  it("stays legacy when mode is absent (not the string 'conversation')", () => {
    render(<PhaseBar board={mkBoard({ state: mkState({ current_stage: "verifikacia" }) })} />);
    expect(screen.getByText("Verifikácia")).toBeInTheDocument();
    expect(screen.queryByText("Kontrola")).not.toBeInTheDocument();
  });

  it("renders exactly the 4 legacy phases — the terminal 'Hotovo' phase never leaks into the legacy strip (STEP 6)", () => {
    // A legacy board that has settled to done: the legacy strip filters the 'done' phase, so 'Hotovo' (the
    // PHASE_LABELS['done'] label) is not shown; only the four real phases render.
    render(<PhaseBar board={mkBoard({ state: mkState({ current_stage: "verifikacia", status: "done" }) })} />);
    for (const label of LEGACY_LABELS) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText("Hotovo")).not.toBeInTheDocument();
  });
});

describe("PhaseBar — conversation (spine) strip derived from board signals (STEP 5)", () => {
  it("renders the redesign four phases (not the legacy labels)", () => {
    render(<PhaseBar board={convBoard({})} />);
    for (const label of CONV_LABELS) expect(screen.getByText(label)).toBeInTheDocument();
    // Legacy-only labels never appear on the conversation strip.
    expect(screen.queryByText("Príprava")).not.toBeInTheDocument();
    expect(screen.queryByText("Návrh")).not.toBeInTheDocument();
    expect(screen.queryByText("Verifikácia")).not.toBeInTheDocument();
  });

  it("highlights Špecifikácia by default (no signals, spec not yet approved)", () => {
    render(<PhaseBar board={convBoard({})} />);
    expect(isCurrent("Špecifikácia")).toBe(true);
    expect(isCurrent("Kontrola")).toBe(false);
  });

  it("highlights Plán when the Špecifikácia is approved but no build/check yet", () => {
    render(<PhaseBar board={convBoard({ specApproved: true })} />);
    expect(isCurrent("Plán")).toBe(true);
    expect(isCurrent("Programovanie")).toBe(false);
  });

  it("highlights Programovanie when the build is ready to start (spustit_stavbu offered)", () => {
    render(<PhaseBar board={convBoard({ specApproved: true, actions: ["spustit_stavbu"] })} />);
    expect(isCurrent("Programovanie")).toBe(true);
  });

  it("highlights Programovanie when the build is running (pause offered)", () => {
    render(<PhaseBar board={convBoard({ actions: ["pause"] })} />);
    expect(isCurrent("Programovanie")).toBe(true);
  });

  it("highlights Programovanie when the build is resumable (pokracovat offered)", () => {
    render(<PhaseBar board={convBoard({ actions: ["pokracovat"], status: "paused" })} />);
    expect(isCurrent("Programovanie")).toBe(true);
  });

  it("highlights Kontrola when the check is offered (skontrolovat)", () => {
    render(<PhaseBar board={convBoard({ actions: ["skontrolovat"] })} />);
    expect(isCurrent("Kontrola")).toBe(true);
    expect(isCurrent("Programovanie")).toBe(false);
  });

  it("highlights Kontrola when a message carries payload.kontrola", () => {
    render(<PhaseBar board={convBoard({ status: "awaiting_manazer", messages: [mkMsg({ kontrola: true })] })} />);
    expect(isCurrent("Kontrola")).toBe(true);
  });

  it("highlights Kontrola when the build just completed and the agent is working (programming_complete + agent_working)", () => {
    render(
      <PhaseBar board={convBoard({ status: "agent_working", messages: [mkMsg({ programming_complete: true })] })} />,
    );
    expect(isCurrent("Kontrola")).toBe(true);
  });

  it("does NOT reach Kontrola on programming_complete alone when the agent is not working", () => {
    // programming_complete without agent_working → the build-complete-then-checking signal is not active;
    // with spec approved this reads as Programovanie (a finished build awaiting the Skontrolovať trigger),
    // never Kontrola.
    render(
      <PhaseBar
        board={convBoard({
          status: "awaiting_manazer",
          specApproved: true,
          currentStage: "programovanie",
          messages: [mkMsg({ programming_complete: true })],
        })}
      />,
    );
    expect(isCurrent("Kontrola")).toBe(false);
    expect(isCurrent("Programovanie")).toBe(true);
  });

  it("Kontrola wins over Programovanie/Plán when several signals overlap (furthest-right priority)", () => {
    render(
      <PhaseBar
        board={convBoard({ specApproved: true, currentStage: "programovanie", actions: ["skontrolovat"] })}
      />,
    );
    expect(isCurrent("Kontrola")).toBe(true);
    expect(isCurrent("Programovanie")).toBe(false);
    expect(isCurrent("Plán")).toBe(false);
  });

  // STEP 6 (Hotovo): the terminal manager sign-off settles the version to status='done'. The conversation strip
  // gains a 5th, rightmost phase 'Hotovo' — current when done, back-marking Kontrola ✓.
  it("renders Hotovo as the 5th terminal phase in the conversation strip", () => {
    render(<PhaseBar board={convBoard({})} />);
    expect(screen.getByText("Hotovo")).toBeInTheDocument();
  });

  it("marks Hotovo current and back-marks Kontrola done (✓) when the conversation version is done", () => {
    render(<PhaseBar board={convBoard({ status: "done" })} />);
    // Hotovo is the current (rightmost) terminal phase; Kontrola is no longer current.
    expect(isCurrent("Hotovo")).toBe(true);
    expect(isCurrent("Kontrola")).toBe(false);
    // The five earlier phases are all done (✓); Hotovo alone is the ● current marker.
    expect(screen.getAllByText("✓")).toHaveLength(5);
    expect(screen.getByText("●")).toBeInTheDocument();
  });
});
