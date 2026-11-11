/**
 * DEV-360 — at each account the Manažér sets who answers for its tickets in Plane.
 *
 * Director 11.11.2026: „Súhlasím, založ tiket a sprav to.“ — on the proposal that each account gets its person, the
 * accounts alex and rausch the Director. An account without one is matched by its names; the row says whom they find.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const usersApi = vi.hoisted(() => ({
  listPlaneResponsibleApi: vi.fn(),
  setPlaneResponsibleApi: vi.fn(),
}));
vi.mock("@/services/api/users", () => usersApi);

import PlaneResponsiblePanel from "@/components/settings/PlaneResponsiblePanel";

const row = (username: string, extra = {}) => ({
  id: `u-${username}`,
  username,
  name: null,
  chosen: null,
  by_name: null,
  ...extra,
});

beforeEach(() => {
  usersApi.listPlaneResponsibleApi.mockReset();
  usersApi.setPlaneResponsibleApi.mockReset();
  usersApi.listPlaneResponsibleApi.mockResolvedValue({
    people: ["Andros Dedo", "Zoltán"],
    accounts: [
      row("alex", { name: "Alexander Honest" }),
      row("zoltan", { name: "Zoltán Rausch", by_name: "Zoltán" }),
    ],
  });
});

describe("Zodpovedný v Plane (DEV-360)", () => {
  it("each account says whom its names find, or that they find nobody", async () => {
    render(<PlaneResponsiblePanel />);

    const alex = await screen.findByRole("combobox", { name: "Zodpovedný v Plane pre konto alex" });
    const zoltan = screen.getByRole("combobox", { name: "Zodpovedný v Plane pre konto zoltan" });

    expect(alex).toHaveDisplayValue("Nenastavený — meno konta v Plane nikoho nenájde");
    expect(zoltan).toHaveDisplayValue("Podľa mena konta: Zoltán");
    expect(within(alex).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Nenastavený — meno konta v Plane nikoho nenájde",
      "Andros Dedo",
      "Zoltán",
    ]);
  });

  it("choosing a person saves it at the account and the row shows it", async () => {
    usersApi.setPlaneResponsibleApi.mockResolvedValue(row("alex", { name: "Alexander Honest", chosen: "Zoltán" }));
    render(<PlaneResponsiblePanel />);

    const alex = await screen.findByRole("combobox", { name: "Zodpovedný v Plane pre konto alex" });
    fireEvent.change(alex, { target: { value: "Zoltán" } });

    expect(usersApi.setPlaneResponsibleApi).toHaveBeenCalledWith("u-alex", "Zoltán");
    expect(await screen.findByDisplayValue("Zoltán")).toBe(alex);
  });

  it("the empty choice clears the person — the account's names decide again", async () => {
    usersApi.listPlaneResponsibleApi.mockResolvedValue({
      people: ["Andros Dedo", "Zoltán"],
      accounts: [row("rausch", { chosen: "Zoltán" })],
    });
    usersApi.setPlaneResponsibleApi.mockResolvedValue(row("rausch"));
    render(<PlaneResponsiblePanel />);

    const rausch = await screen.findByRole("combobox", { name: "Zodpovedný v Plane pre konto rausch" });
    expect(rausch).toHaveDisplayValue("Zoltán");
    fireEvent.change(rausch, { target: { value: "" } });

    expect(usersApi.setPlaneResponsibleApi).toHaveBeenCalledWith("u-rausch", null);
  });

  it("when Plane does not answer, the panel says so", async () => {
    usersApi.listPlaneResponsibleApi.mockRejectedValue(new Error("Plane neodpovedá"));
    render(<PlaneResponsiblePanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Zodpovedných v Plane sa nepodarilo načítať/);
  });
});
