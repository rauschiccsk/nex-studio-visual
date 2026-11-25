/**
 * DEV-513 — a project's Poradca offers documents from the internet and „Stiahnuť do projektu“ downloads them into the
 * project. 25.11.2026 NEX DualBook was founded without the bank's integration manual, left to the Manažér by hand.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PoradcaReferencesOffer from "@/components/poradca/PoradcaReferencesOffer";
import { handOffOffer } from "@/lib/poradcaHandoff";
import type { PoradcaMessage } from "@/types/poradca";

const { addMock } = vi.hoisted(() => ({ addMock: vi.fn() }));

vi.mock("@/services/api/poradca", () => ({ addPoradcaReferencesApi: addMock }));

const BANK = {
  path: "docs/reference/banka/businessnet-connect-prirucka.pdf",
  url: "https://www.unicreditbank.sk/c/prirucka-2026.pdf",
  description: "Integračná príručka BusinessNet Connect",
};

describe("PoradcaReferencesOffer (DEV-513)", () => {
  beforeEach(() => addMock.mockReset());

  it("lists the documents linked to their source and downloads them into the project", async () => {
    addMock.mockResolvedValue({ started: true, count: 1 });
    render(<PoradcaReferencesOffer messageId="m1" references={[BANK]} />);

    const offer = screen.getByRole("region", { name: "Podklady na stiahnutie" });
    expect(within(offer).getByRole("link", { name: BANK.description })).toHaveAttribute("href", BANK.url);
    fireEvent.click(screen.getByRole("button", { name: "Stiahnuť do projektu" }));

    expect(
      await screen.findByText(
        "Kokpit sťahuje podklady do projektu — čo prišlo, uvidíš na stránke verzie v časti „Podklady z internetu“.",
      ),
    ).toBeInTheDocument();
    expect(addMock).toHaveBeenCalledWith("m1");
  });

  it("says to try again when a download for the project is already going on", async () => {
    addMock.mockResolvedValue({ started: false, count: 1 });
    render(<PoradcaReferencesOffer messageId="m1" references={[BANK]} />);

    fireEvent.click(screen.getByRole("button", { name: "Stiahnuť do projektu" }));

    expect(
      await screen.findByText(
        "Kokpit práve sťahuje podklady projektu — keď skončí, stlač „Stiahnuť do projektu“ znova.",
      ),
    ).toBeInTheDocument();
  });

  it("an answer whose list is not complete hands nothing on", () => {
    const message = {
      id: "m2",
      author: "poradca",
      content: "Pokyn.",
      status: "done",
      instruction: "Urob to.",
      incomplete_references: "Kokpit neponúka odpoveď, ktorej zoznam podkladov z internetu nie je úplný: …",
      choices: [],
    } as unknown as PoradcaMessage;

    expect(handOffOffer(message).instruction).toBeNull();
  });
});
