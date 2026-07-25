/**
 * ReverifyNoFixBar — the clean "re-run the Verifikácia gate" exit (overit_bez_opravy). Two contexts share
 * the SAME action with CONTEXT-AWARE copy (v4.0.49): a programovanie fix-loop ("chyba mimo projektu") vs a
 * verifikacia Auditor-stall ("overenie sa zaseklo — spusti ho znova"). Honest-by-construction: renders only
 * when the backend offers `overit_bez_opravy`. Pins that the stall case never shows the "mimo projektu" copy.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ReverifyNoFixBar from "@/components/riadiace/ReverifyNoFixBar";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

function boardWith(actions: string[], stage?: string): PipelineBoard {
  const state = stage ? { current_stage: stage } : null;
  return { state, recent_messages: [], available_actions: actions } as unknown as PipelineBoard;
}
const NEXT_BOARD = { state: { current_stage: "verifikacia" } } as unknown as PipelineBoard;

describe("ReverifyNoFixBar — clean re-verify (context-aware copy)", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(postPipelineActionApi).mockResolvedValue(NEXT_BOARD);
  });

  it("renders NOTHING when overit_bez_opravy is not offered (honest-by-construction)", () => {
    const { container } = render(
      <ReverifyNoFixBar board={boardWith(["uprav", "ask"])} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("at a verifikacia stall shows the 'Znova spustiť overenie' copy, NOT 'mimo projektu'", () => {
    render(
      <ReverifyNoFixBar board={boardWith(["overit_bez_opravy"], "verifikacia")} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /Znova spustiť overenie/ })).toBeInTheDocument();
    expect(screen.getByText(/zasekol/)).toBeInTheDocument();
    expect(screen.queryByText(/mimo tohto projektu/)).not.toBeInTheDocument();
  });

  it("in a programovanie fix-loop shows the 'mimo projektu' copy", () => {
    render(
      <ReverifyNoFixBar board={boardWith(["overit_bez_opravy"], "programovanie")} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /Znova overiť bez opravy/ })).toBeInTheDocument();
    expect(screen.getByText(/mimo tohto projektu/)).toBeInTheDocument();
  });

  it("click fires overit_bez_opravy and adopts the returned board", async () => {
    const onBoard = vi.fn();
    render(
      <ReverifyNoFixBar board={boardWith(["overit_bez_opravy"], "verifikacia")} versionId="v-1" onBoard={onBoard} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Znova spustiť overenie/ }));
    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "overit_bez_opravy" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });
});
