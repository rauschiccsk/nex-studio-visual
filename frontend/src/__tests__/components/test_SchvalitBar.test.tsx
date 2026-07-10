/**
 * SchvalitBar — the "Schváliť plán" moment at the Návrh/plan-approval gate (regression fix
 * schvalit-approval-bar.md).
 *
 * Honest-by-construction gate: the bar renders ONLY when the backend currently OFFERS `schvalit` in
 * `board.available_actions` (a Návrh gate awaiting the manager, `available_actions={uprav, ask, schvalit}`).
 * Primary "Schváliť plán" → `postPipelineActionApi(action:"schvalit")` advances Návrh → Programovanie;
 * secondary "Upraviť" → `postPipelineActionApi(action:"uprav")` sends the comment back as the rework
 * instruction. The optional comment threads into `payload.comment`; `onBoard` adopts the returned board.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import SchvalitBar from "@/components/riadiace/SchvalitBar";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/services/api/pipeline", () => ({
  postPipelineActionApi: vi.fn(),
}));

// A board offering `actions` (honest-by-construction: the bar keys off available_actions only). `stage`
// drives the CONTEXT-AWARE copy (release-smoke-boot-and-batch-fixes.md C); omitting it (state:null) is the
// legacy/Návrh default that keeps today's plan copy.
function boardWith(actions: string[], stage?: string): PipelineBoard {
  const state = stage ? { current_stage: stage } : null;
  return { state, recent_messages: [], available_actions: actions } as unknown as PipelineBoard;
}

// The fresh board the action returns — onBoard must adopt exactly this.
const NEXT_BOARD = { state: { current_stage: "programovanie" } } as unknown as PipelineBoard;

const APPROVE = /Schváliť plán/;
const REWORK = /Upraviť/;

describe("SchvalitBar — the Návrh/plan-approval gate button", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(postPipelineActionApi).mockResolvedValue(NEXT_BOARD);
    navigateMock.mockReset();
  });

  it("renders NOTHING when `schvalit` is not offered (honest-by-construction)", () => {
    const { container } = render(
      <SchvalitBar board={boardWith(["approve_spec"])} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("button", { name: APPROVE })).not.toBeInTheDocument();
  });

  it("renders NOTHING when board is null", () => {
    const { container } = render(<SchvalitBar board={null} versionId="v-1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders BOTH the 'Schváliť plán' and 'Upraviť' buttons when `schvalit` is offered", () => {
    render(<SchvalitBar board={boardWith(["schvalit", "uprav", "ask"])} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByRole("button", { name: APPROVE })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: REWORK })).toBeInTheDocument();
  });

  it("click 'Schváliť plán' fires `schvalit` (no comment → no payload) and adopts the returned board", async () => {
    const onBoard = vi.fn();
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"])} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: APPROVE }));

    await waitFor(() =>
      expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "schvalit", payload: undefined }),
    );
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  it("click 'Schváliť plán' threads a typed comment into payload.comment", async () => {
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"])} versionId="v-1" onBoard={vi.fn()} />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "  vyzerá dobre  " } });
    fireEvent.click(screen.getByRole("button", { name: APPROVE }));

    await waitFor(() =>
      expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", {
        action: "schvalit",
        payload: { comment: "vyzerá dobre" },
      }),
    );
  });

  it("click 'Upraviť' fires `uprav` with the comment as the rework instruction", async () => {
    const onBoard = vi.fn();
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"])} versionId="v-1" onBoard={onBoard} />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "prepracuj plán úloh" } });
    fireEvent.click(screen.getByRole("button", { name: REWORK }));

    await waitFor(() =>
      expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", {
        action: "uprav",
        payload: { comment: "prepracuj plán úloh" },
      }),
    );
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  // ── Context-aware copy (release-smoke-boot-and-batch-fixes.md C): the SAME `schvalit` verb, two gates ──

  it("at the Návrh gate (current_stage 'navrh') shows the PLAN copy", () => {
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"], "navrh")} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Schváliť plán/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Prejsť na overenie/ })).not.toBeInTheDocument();
    expect(screen.getByText(/posunie do stavby \(Programovanie\)/)).toBeInTheDocument();
  });

  it("at a COMPLETED build (current_stage 'programovanie') shows the VERIFICATION copy", () => {
    render(
      <SchvalitBar board={boardWith(["schvalit", "uprav"], "programovanie")} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /Prejsť na overenie/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Schváliť plán/ })).not.toBeInTheDocument();
    expect(screen.getByText(/posunie na Verifikáciu \(overenie Auditorom\)/)).toBeInTheDocument();
    // The action stays `schvalit` regardless of the copy.
    fireEvent.click(screen.getByRole("button", { name: /Prejsť na overenie/ }));
    return waitFor(() =>
      expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "schvalit", payload: undefined }),
    );
  });

  it("defaults to the PLAN copy when state is null (legacy board)", () => {
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"])} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Schváliť plán/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Prejsť na overenie/ })).not.toBeInTheDocument();
  });

  it("the review affordance navigates to /specifikacia", () => {
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"])} versionId="v-1" onBoard={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Prezrieť plán/ }));
    expect(navigateMock).toHaveBeenCalledWith("/specifikacia");
  });

  it("surfaces an error when the action rejects (no board adopted)", async () => {
    vi.mocked(postPipelineActionApi).mockRejectedValueOnce(new Error("Schválenie zlyhalo."));
    const onBoard = vi.fn();
    render(<SchvalitBar board={boardWith(["schvalit", "uprav"])} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: APPROVE }));

    await waitFor(() => expect(screen.getByText("Schválenie zlyhalo.")).toBeInTheDocument());
    expect(onBoard).not.toHaveBeenCalled();
  });
});
