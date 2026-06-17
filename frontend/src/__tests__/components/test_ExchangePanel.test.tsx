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

  // R4 (D2): the blocked banner is precise from block_reason — question vs each error class.
  it("blocked + block_reason=agent_question → Director prompt banner", () => {
    const board = mkBoard("gate_a", "designer", "blocked");
    board.state!.block_reason = "agent_question";
    render(<ExchangePanel board={board} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Na rade: Director — odpovedz Návrhár-ovi")).toBeInTheDocument();
  });

  it("blocked + block_reason=agent_error → 'Agent zlyhal … skús znova'", () => {
    const board = mkBoard("gate_a", "designer", "blocked");
    board.state!.block_reason = "agent_error";
    render(<ExchangePanel board={board} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Agent zlyhal vo fáze Rozsah — skús znova")).toBeInTheDocument();
  });

  it("blocked + block_reason=system_error → 'Systémová chyba … skús znova'", () => {
    const board = mkBoard("gate_a", "designer", "blocked");
    board.state!.block_reason = "system_error";
    render(<ExchangePanel board={board} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Systémová chyba vo fáze Rozsah — skús znova")).toBeInTheDocument();
  });

  it("blocked + block_reason=parse_exhaustion → 'Chyba spracovania výstupu … skús znova'", () => {
    const board = mkBoard("gate_a", "designer", "blocked");
    board.state!.block_reason = "parse_exhaustion";
    render(<ExchangePanel board={board} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText("Chyba spracovania výstupu vo fáze Rozsah — skús znova")).toBeInTheDocument();
  });

  // CR-NS-057 §F2.4: ExchangePanel threads board.regate_proposal to the action bar + the whos-turn board.
  it("gate_g with a regate_proposal → threads it (FAIL→target button + the whos-turn proposal line)", () => {
    const board = mkBoard("gate_g", "auditor", "awaiting_director");
    board.available_actions = ["verdict", "ask"];
    board.regate_proposal = { entry_stage: "gate_a", reason: "návrh/rozsah → späť na dizajn" };
    render(<ExchangePanel board={board} inFlight={false} activity={[]} onAction={vi.fn()} />);
    expect(screen.getByText(/Verdikt FAIL → Rozsah/)).toBeInTheDocument(); // PipelineActionBar received it
    expect(screen.getByText(/Navrhovaný návrat pri FAIL: Rozsah/)).toBeInTheDocument(); // WhosTurnBoard received it
  });
});

describe("ExchangePanel — unified banner colours (CR-NS-028)", () => {
  it("agent_working banner uses the blue tone (not emerald)", () => {
    render(<ExchangePanel board={mkBoard("gate_a", "designer", "agent_working")} inFlight={false} activity={[]} onAction={vi.fn()} />);
    const banner = screen.getByText("Návrhár pracuje na fáze Rozsah").closest("div")!;
    expect(banner).toHaveClass("bg-sky-500/10"); // blue = working
    expect(banner).not.toHaveClass("bg-emerald-500/10"); // no emerald-for-working
  });

  it("done banner uses the green tone + the whos-turn board is gated off (no turn at done)", () => {
    render(<ExchangePanel board={mkBoard("release", "director", "done")} inFlight={false} activity={[]} onAction={vi.fn()} />);
    const banner = screen.getByText("Hotovo").closest("div")!;
    expect(banner).toHaveClass("bg-emerald-500/10"); // green = done
    // WhosTurnBoard (CR-NS-035) is not rendered at done — its markers are absent
    expect(screen.queryByText(/fáza/)).not.toBeInTheDocument();
    expect(screen.queryByText("cez Koordinátora")).not.toBeInTheDocument();
  });
});

describe("ExchangePanel — live activity feed below the thread (CR-NS-026)", () => {
  it("renders the activity feed AFTER the thread and ABOVE the action bar while agent_working", () => {
    render(
      <ExchangePanel
        board={mkBoard("build", "implementer", "agent_working")}
        inFlight={false}
        activity={[{ stage: "build", actor: "implementer", kind: "tool", line: "číta docs/spec.md" }]}
        onAction={vi.fn()}
      />,
    );
    const thread = screen.getByText("Zatiaľ žiadne správy v pipeline.");
    const feed = screen.getByText("Živá aktivita agenta");
    const actionBar = screen.getByText("Pauza"); // build/agent_working action (CR-NS-027)
    // DOM order top-to-bottom: thread → live activity → action bar
    expect(thread.compareDocumentPosition(feed) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(feed.compareDocumentPosition(actionBar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("does not render the activity feed when not agent_working", () => {
    render(
      <ExchangePanel
        board={mkBoard("build", "implementer", "awaiting_director")}
        inFlight={false}
        activity={[{ stage: "build", actor: "implementer", kind: "tool", line: "x" }]}
        onAction={vi.fn()}
      />,
    );
    expect(screen.queryByText("Živá aktivita agenta")).not.toBeInTheDocument();
  });
});
