/**
 * DEV-483 — the admin switches the fast path with Poradca per account; it starts off.
 *
 * Director 21.11.2026: „Chcem aby táto možnosť bola nastaviteľná pre každého používateľa osobitne. Dôvodom je že pre
 * Tibora a Nazara chcem zatiaľ ponechať terajší spôsob.“
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const api = vi.hoisted(() => ({ listBatchReviewAccountsApi: vi.fn(), setBatchReviewApi: vi.fn() }));
vi.mock("@/services/api/batchReview", () => api);

import BatchReviewPanel from "@/components/settings/BatchReviewPanel";

const ZOLTAN = { id: "u1", username: "zoltan", name: "Zoltán Rausch", on: false };
const TIBOR = { id: "u2", username: "tibor", name: null, on: false };

beforeEach(() => {
  api.listBatchReviewAccountsApi.mockReset().mockResolvedValue([ZOLTAN, TIBOR]);
  api.setBatchReviewApi.mockReset();
});

describe("Otázky Staviteľa najprv s Poradcom — per account (DEV-483)", () => {
  it("lists every account switched off and switches one on alone", async () => {
    api.setBatchReviewApi.mockResolvedValue({ ...ZOLTAN, on: true });
    render(<BatchReviewPanel />);

    const zoltan = await screen.findByRole("checkbox", { name: "Otázky Staviteľa najprv s Poradcom pre konto zoltan" });
    expect(zoltan).not.toBeChecked();
    fireEvent.click(zoltan);

    await waitFor(() => expect(zoltan).toBeChecked());
    expect(api.setBatchReviewApi).toHaveBeenCalledWith("u1", true);
    expect(screen.getByRole("checkbox", { name: "Otázky Staviteľa najprv s Poradcom pre konto tibor" })).not.toBeChecked();
  });

  it("says when the switch could not be saved", async () => {
    api.setBatchReviewApi.mockRejectedValue(new Error("x"));
    render(<BatchReviewPanel />);

    fireEvent.click(await screen.findByRole("checkbox", { name: /pre konto tibor/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Nastavenie sa nepodarilo uložiť");
  });
});
