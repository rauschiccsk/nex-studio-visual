/**
 * BlockRecoveryBar — the box the Manažér answers a blocked build in (ICCINT-10).
 *
 * It used to be a single-line ``<input>``: a five-sentence answer scrolled sideways through a 1.5rem
 * slot with the text already typed invisible, plain Enter sent whatever was there, and Shift+Enter
 * sent it too — so a paragraph could leave half-written with no way to add a second line. The Director
 * hit all three while answering the AI Agent on 22.08.2026.
 *
 * These pin the behaviour, not the markup: it must be a textarea, Enter must send, Shift+Enter must
 * not, and Slovak must not be underlined.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { postPipelineActionApi } = vi.hoisted(() => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/pipeline", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  postPipelineActionApi,
}));

import BlockRecoveryBar from "@/components/riadiace/BlockRecoveryBar";
import type { PipelineBoard } from "@/services/api/pipeline";

function board(blockReason: string): PipelineBoard {
  return {
    state: {
      status: "blocked",
      block_reason: blockReason,
      current_stage: "programovanie",
      next_action: "Odpovedz agentovi.",
    },
  } as unknown as PipelineBoard;
}

function renderBar(reason = "agent_question") {
  const onBoard = vi.fn();
  render(<BlockRecoveryBar board={board(reason)} versionId="v1" onBoard={onBoard} />);
  return { onBoard };
}

beforeEach(() => {
  postPipelineActionApi.mockReset();
  postPipelineActionApi.mockResolvedValue(board("agent_question"));
});

describe("BlockRecoveryBar", () => {
  it("is a textarea, not a one-line input — a long answer has to be readable while it is written", () => {
    renderBar();
    const box = screen.getByPlaceholderText(/Tvoja odpoveď/);
    expect(box.tagName).toBe("TEXTAREA");
  });

  it("is spellchecked as Slovak — it is prose, not an identifier", () => {
    renderBar();
    // An answer to the AI Agent is sentences, so a dictionary has something useful to say. It is marked
    // lang="sk" so the browser reaches for the SLOVAK dictionary; checking Slovak against English was
    // what underlined every correct word in the first place.
    const box = screen.getByPlaceholderText(/Tvoja odpoveď/);
    expect(box).toHaveAttribute("spellcheck", "true");
    expect(box).toHaveAttribute("lang", "sk");
  });

  it("Enter sends", async () => {
    renderBar();
    const box = screen.getByPlaceholderText(/Tvoja odpoveď/);
    fireEvent.change(box, { target: { value: "Áno, súhlasím" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledTimes(1));
    expect(postPipelineActionApi).toHaveBeenCalledWith("v1", {
      action: "answer",
      payload: { text: "Áno, súhlasím" },
    });
  });

  it("Shift+Enter does NOT send — that is how a second paragraph gets written", () => {
    renderBar();
    const box = screen.getByPlaceholderText(/Tvoja odpoveď/);
    fireEvent.change(box, { target: { value: "Prvý riadok" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });

    expect(postPipelineActionApi).not.toHaveBeenCalled();
  });

  it("says which key does what, so nobody has to discover it by losing a paragraph", () => {
    renderBar();
    expect(screen.getByPlaceholderText(/Enter odošle, Shift\+Enter nový riadok/)).toBeInTheDocument();
  });

  it("an empty question answer is not sendable, an empty error steer is", () => {
    const { unmount } = render(
      <BlockRecoveryBar board={board("agent_question")} versionId="v1" onBoard={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /Odpovedať/ })).toBeDisabled();
    unmount();

    render(<BlockRecoveryBar board={board("agent_error")} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Skús znova/ })).toBeEnabled();
  });
});
