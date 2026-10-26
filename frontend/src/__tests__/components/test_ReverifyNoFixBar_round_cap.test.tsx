/**
 * DEV-231 — on the Verifikácia round cap the re-verify bar speaks of a cause outside the project, not of a stall.
 *
 * 26.10.2026 Author Asistent 0.1.0 stood on the cap („Auditor po 5 kolách stále FAIL“) after three re-verifies the
 * cockpit's own bugs had forced; the board now offers the re-verify there too. The bar chose its words by the stage
 * alone, and in Verifikácia it said „Overenie sa zaseklo … nie je to chyba aplikácie, tá prešla skúškou po spustení“ —
 * untrue on the cap, where the check DID run and failed.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ReverifyNoFixBar from "@/components/riadiace/ReverifyNoFixBar";
import type { PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const board = (block_reason: string) =>
  ({
    state: { current_stage: "verifikacia", status: "blocked", block_reason },
    recent_messages: [],
    available_actions: ["decide", "overit_bez_opravy"],
  }) as unknown as PipelineBoard;

describe("ReverifyNoFixBar — the round cap (DEV-231)", () => {
  it("on the cap card says the cause may have been outside the project and offers the re-verify", () => {
    render(<ReverifyNoFixBar board={board("decision_needed")} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByText("Chyba bola mimo projektu? Znova over bez opravy")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Znova overiť bez opravy" })).toBeInTheDocument();
    expect(screen.queryByText(/zaseklo/)).not.toBeInTheDocument();
  });

  it("a stalled check in Verifikácia keeps its own words", () => {
    render(<ReverifyNoFixBar board={board("agent_error")} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByText("Overenie sa zaseklo — spusti ho znova")).toBeInTheDocument();
  });
});
