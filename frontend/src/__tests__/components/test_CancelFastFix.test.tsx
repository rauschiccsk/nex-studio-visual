/**
 * CancelFastFix — „Zrušiť rýchlu opravu“ (DEV-491).
 *
 * 22.11.2026 the Director founded a fast fix of Orthodox Hermenia by mistake (version 1.1.1, the brief only
 * „Oprava chyby v dodanom kóde“) and found no way to take it back: „Nevidím nikde možnosť ako zrušiť verziu 1.1.1“.
 * The button exists on every unfinished fast fix; the backend decides whether it takes the cancel and why not.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { cancelFastFixApi } = vi.hoisted(() => ({ cancelFastFixApi: vi.fn() }));
vi.mock("@/services/api/pipeline", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  cancelFastFixApi,
}));

import CancelFastFix from "@/components/controlCenter/CancelFastFix";
import { ApiError } from "@/services/api";
import type { PipelineBoard } from "@/services/api/pipeline";

const WORKING = "Staviteľ na rýchlej oprave práve pracuje — zrušiť ju pôjde, keď ťah skončí.";

function board(state: Record<string, unknown>): PipelineBoard {
  return {
    state: {
      flow_type: "fast_fix",
      current_stage: "priprava",
      status: "blocked",
      cancellable: true,
      cancel_refusal: null,
      ...state,
    },
    available_actions: [],
    recent_messages: [],
  } as unknown as PipelineBoard;
}

function renderIt(state: Record<string, unknown> = {}, onCancelled = vi.fn()) {
  render(<CancelFastFix board={board(state)} versionId="v9" versionNumber="1.1.1" onCancelled={onCancelled} />);
  return onCancelled;
}

const opener = () => screen.getByRole("button", { name: "Zrušiť rýchlu opravu" });

beforeEach(() => {
  cancelFastFixApi.mockReset();
  cancelFastFixApi.mockResolvedValue({ version_number: "1.1.1", plane_warning: null });
});

describe("CancelFastFix", () => {
  it("is not there on a regular version, nor on a finished fix", () => {
    const { container: regular } = render(
      <CancelFastFix board={board({ flow_type: "new_version" })} versionId="v" versionNumber="2.0.0" onCancelled={vi.fn()} />,
    );
    const { container: done } = render(
      <CancelFastFix board={board({ current_stage: "done" })} versionId="v" versionNumber="1.1.1" onCancelled={vi.fn()} />,
    );
    expect(regular).toBeEmptyDOMElement();
    expect(done).toBeEmptyDOMElement();
  });

  it("is greyed with the backend's reason when the fix cannot be cancelled now", () => {
    renderIt({ cancellable: false, cancel_refusal: WORKING });
    expect(opener()).toBeDisabled();
    expect(opener()).toHaveAttribute("title", WORKING);
  });

  it("asks first and cancels nothing on „Ponechať“", () => {
    const onCancelled = renderIt();
    fireEvent.click(opener());

    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Rýchla oprava 1.1.1 ešte nič neurobila. Zruší sa celá aj s rozhovorom so Staviteľom a ďalšia rýchla oprava " +
        "projektu dostane znova číslo 1.1.1.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Ponechať" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(cancelFastFixApi).not.toHaveBeenCalled();
    expect(onCancelled).not.toHaveBeenCalled();
  });

  it("cancels the fix on confirmation and leaves its Riadiace centrum", async () => {
    const onCancelled = renderIt();
    fireEvent.click(opener());
    fireEvent.click(screen.getAllByRole("button", { name: "Zrušiť rýchlu opravu" })[1]!);

    await waitFor(() => expect(onCancelled).toHaveBeenCalledTimes(1));
    expect(cancelFastFixApi).toHaveBeenCalledWith("v9");
  });

  it("when the ticket did not go back, says so before leaving", async () => {
    const warning = "Rýchla oprava je zrušená, ale tiket DEV-489 sa v Plane nevrátil do Todo: Plane neodpovedá.";
    cancelFastFixApi.mockResolvedValue({ version_number: "1.1.1", plane_warning: warning });
    const onCancelled = renderIt();
    fireEvent.click(opener());
    fireEvent.click(screen.getAllByRole("button", { name: "Zrušiť rýchlu opravu" })[1]!);

    expect(await screen.findByRole("status")).toHaveTextContent(warning);
    expect(onCancelled).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Rozumiem" }));
    expect(onCancelled).toHaveBeenCalledTimes(1);
  });

  it("a refusal from the backend stays in the dialog in words, the fix stays", async () => {
    cancelFastFixApi.mockRejectedValue(new ApiError(409, "Conflict", { detail: WORKING }));
    const onCancelled = renderIt();
    fireEvent.click(opener());
    fireEvent.click(screen.getAllByRole("button", { name: "Zrušiť rýchlu opravu" })[1]!);

    expect(await screen.findByText(/Zrušenie rýchlej opravy zlyhalo/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent(WORKING);
    expect(onCancelled).not.toHaveBeenCalled();
  });
});
