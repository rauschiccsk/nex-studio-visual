/**
 * ZopakovatKonzultaciuBar — the way back when the Decision Cards could not be built (ICCINT-25).
 *
 * The Director hit this on nex-productcatalogs: an outage killed both attempts at turning eleven review
 * findings into cards, and once it passed there was no way to ask again. The screen kept a wall of findings
 * and two buttons where one-question-at-a-time was supposed to be.
 *
 * Honest-by-construction like every other riadiace bar: it exists only while the backend says the retry is
 * actually available, so it can never become a button that refuses when pressed.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { postPipelineActionApi } = vi.hoisted(() => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/pipeline", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  postPipelineActionApi,
}));

import ZopakovatKonzultaciuBar from "@/components/riadiace/ZopakovatKonzultaciuBar";
import type { PipelineBoard } from "@/services/api/pipeline";

function board(actions: string[]): PipelineBoard {
  return {
    state: { status: "awaiting_manazer", current_stage: "navrh", next_action: "…" },
    available_actions: actions,
    recent_messages: [],
  } as unknown as PipelineBoard;
}

beforeEach(() => {
  postPipelineActionApi.mockReset();
  postPipelineActionApi.mockResolvedValue(board([]));
});

describe("ZopakovatKonzultaciuBar", () => {
  it("renders nothing when the backend is not offering the retry", () => {
    const { container } = render(
      <ZopakovatKonzultaciuBar board={board(["ask", "uprav", "schvalit"])} versionId="v1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("offers the one move when the backend says the cards can be asked for again", () => {
    render(
      <ZopakovatKonzultaciuBar
        board={board(["ask", "uprav", "schvalit", "zopakovat_konzultaciu"])}
        versionId="v1"
        onBoard={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Skúsiť konzultáciu znova/ })).toBeEnabled();
  });

  it("says the findings are not lost — that is the difference between this and an error", () => {
    render(
      <ZopakovatKonzultaciuBar board={board(["zopakovat_konzultaciu"])} versionId="v1" onBoard={vi.fn()} />,
    );
    // A non-expert must not read "it failed" as "the review is gone".
    expect(screen.getByText(/nič nestratilo/)).toBeInTheDocument();
  });

  it("pressing it asks the backend for the consultation again", async () => {
    const onBoard = vi.fn();
    render(<ZopakovatKonzultaciuBar board={board(["zopakovat_konzultaciu"])} versionId="v7" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Skúsiť konzultáciu znova/ }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledTimes(1));
    expect(postPipelineActionApi).toHaveBeenCalledWith("v7", { action: "zopakovat_konzultaciu" });
    await waitFor(() => expect(onBoard).toHaveBeenCalled());
  });
});
