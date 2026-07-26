/**
 * ReverifyBar — the drift re-verify surface ("Over znova", CR-V2-057). It had NO test until v4.0.57, when
 * its chrome was extracted into the shared `WarningActionBar` alongside DeployBlockNotice. Refactoring a
 * shipped, untested component is the risk; these close it by pinning the behaviour that must survive:
 *
 *   - honest-by-construction: nothing renders unless the backend OFFERS `overit_znovu` right now;
 *   - the copy is DRIFT-SHAPE aware — a conversation build's `hotovo_drift` promises the automatic re-sign,
 *     a phase build's `sha_drift` promises an independent Auditor re-run. Saying the wrong one would
 *     mis-set what the manager expects to happen after the click;
 *   - the action posts `overit_znovu` and hands the returned board upward;
 *   - a failure surfaces a plain-Slovak error and does NOT pretend the run started.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ReverifyBar from "@/components/riadiace/ReverifyBar";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

function boardWith(actions: string[], provenance?: string): PipelineBoard {
  return {
    state: { current_stage: "done", status: "done" },
    recent_messages: [],
    available_actions: actions,
    verified_provenance: provenance,
  } as unknown as PipelineBoard;
}
const NEXT_BOARD = { state: { current_stage: "verifikacia" } } as unknown as PipelineBoard;

const REVERIFY = /Over znova/;

describe("ReverifyBar — stale verification, and the one click that fixes it", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(postPipelineActionApi).mockResolvedValue(NEXT_BOARD);
  });

  it("renders NOTHING when overit_znovu is not offered (honest-by-construction)", () => {
    const { container } = render(
      <ReverifyBar board={boardWith(["uprav", "ask"], "sha_drift")} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING without a board at all", () => {
    const { container } = render(<ReverifyBar board={null} versionId="v-1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("warns that the verification is stale and offers the re-run", () => {
    render(<ReverifyBar board={boardWith(["overit_znovu"], "sha_drift")} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByText(/Overenie je zastarané — kód sa odvtedy zmenil/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: REVERIFY })).toBeInTheDocument();
  });

  it("on a hotovo_drift promises the automatic re-sign (the conversation-build shape)", () => {
    render(<ReverifyBar board={boardWith(["overit_znovu"], "hotovo_drift")} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByText(/automaticky znovu označí ako hotová/)).toBeInTheDocument();
    expect(screen.queryByText(/nechá Audítora zopakovať overenie/)).not.toBeInTheDocument();
  });

  it("on a sha_drift promises the independent Auditor re-run instead", () => {
    render(<ReverifyBar board={boardWith(["overit_znovu"], "sha_drift")} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByText(/nechá Audítora zopakovať overenie/)).toBeInTheDocument();
    expect(screen.queryByText(/automaticky znovu označí ako hotová/)).not.toBeInTheDocument();
  });

  it("click posts overit_znovu for this version and adopts the returned board", async () => {
    const onBoard = vi.fn();
    render(<ReverifyBar board={boardWith(["overit_znovu"], "sha_drift")} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: REVERIFY }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "overit_znovu" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  it("surfaces a plain-Slovak error and does NOT hand up a board when the re-run fails", async () => {
    vi.mocked(postPipelineActionApi).mockRejectedValueOnce(new Error("boom"));
    const onBoard = vi.fn();
    render(<ReverifyBar board={boardWith(["overit_znovu"], "sha_drift")} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: REVERIFY }));

    await waitFor(() => expect(screen.getByText(/Opätovné overenie zlyhalo/)).toBeInTheDocument());
    expect(onBoard).not.toHaveBeenCalled();
  });
});
