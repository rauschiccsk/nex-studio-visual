/**
 * AdoptProjectDialog — prevzatie existujúceho projektu jedným výberom (ICCINT-85).
 *
 * Prevzatie sa dovtedy robilo formulárom pre ZAKLADANIE: manažér musel ručne prepísať porty, repozitár
 * aj typ, a keď sa pomýlil, evidencia začala tvrdiť niečo iné než disk. Director 09.09.2026: „zadám len
 * názov projektu — napríklad nex-manager — a všetko ostatné urobí systém.“
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

const { listAdoptableApiMock, previewAdoptionApiMock, createProjectApiMock } = vi.hoisted(() => ({
  listAdoptableApiMock: vi.fn(),
  previewAdoptionApiMock: vi.fn(),
  createProjectApiMock: vi.fn(),
}));

vi.mock("@/services/api/projects", () => ({
  listAdoptableApi: listAdoptableApiMock,
  previewAdoptionApi: previewAdoptionApiMock,
  createProjectApi: createProjectApiMock,
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ user: { id: "u-admin", username: "admin" } }),
}));

import AdoptProjectDialog from "@/components/project/AdoptProjectDialog";

const nahlad = {
  slug: "nex-manager",
  source_path: "/opt/projects/nex-manager",
  name: "NEX Manager",
  description: null,
  repo_url: "https://github.com/rauschiccsk/nex-manager.git",
  backend_port: 10210,
  frontend_port: 10211,
  db_port: 10212,
  unresolved: ["spôsob prihlasovania — z disku sa nedá zistiť, potvrď ho"],
  notes: ["názov z prvého nadpisu v CLAUDE.md"],
};

beforeEach(() => {
  vi.clearAllMocks();
  listAdoptableApiMock.mockResolvedValue([
    { slug: "nex-manager", source_path: "/opt/projects/nex-manager", name: "NEX Manager" },
  ]);
  previewAdoptionApiMock.mockResolvedValue(nahlad);
  createProjectApiMock.mockResolvedValue({ id: "p9", slug: "nex-manager" });
});

function dialog(onAdopted = vi.fn()) {
  return <AdoptProjectDialog open onClose={vi.fn()} onAdopted={onAdopted} />;
}

describe("AdoptProjectDialog", () => {
  it("ponúka to, čo na disku naozaj je — manažér nič nepíše naspamäť", async () => {
    render(dialog());
    expect(await screen.findByRole("option", { name: /NEX Manager \(nex-manager\)/ })).toBeInTheDocument();
  });

  it("po výbere ukáže, ČO našiel — pred potvrdením, nie po ňom", async () => {
    render(dialog());
    await screen.findByRole("option", { name: /nex-manager/ });
    await userEvent.selectOptions(screen.getByLabelText(/priečinok/i), "nex-manager");

    expect(await screen.findByText("10210 / 10211 / 10212", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/rauschiccsk\/nex-manager/)).toBeInTheDocument();
  });

  it("povie nahlas aj to, čo sa prečítať NEDALO", async () => {
    // Uhádnutý údaj by vyzeral ako zistený a manažér by ho odklikol bez pozretia.
    render(dialog());
    await screen.findByRole("option", { name: /nex-manager/ });
    await userEvent.selectOptions(screen.getByLabelText(/priečinok/i), "nex-manager");

    // Presná veta zo zoznamu nedopovedaného — nie len slovo, ktoré stojí aj v popiske políčka nižšie.
    expect(
      await screen.findByText("spôsob prihlasovania — z disku sa nedá zistiť, potvrď ho"),
    ).toBeInTheDocument();
  });

  it("preberá s tým, čo prečítal — nie s tým, čo by si manažér musel prepísať", async () => {
    const onAdopted = vi.fn();
    render(dialog(onAdopted));
    await screen.findByRole("option", { name: /nex-manager/ });
    await userEvent.selectOptions(screen.getByLabelText(/priečinok/i), "nex-manager");
    await screen.findByText("10210 / 10211 / 10212", { exact: false });
    await userEvent.click(screen.getByRole("button", { name: /prevziať projekt/i }));

    await waitFor(() => expect(createProjectApiMock).toHaveBeenCalledTimes(1));
    const payload = createProjectApiMock.mock.calls[0]?.[0];
    expect(payload).toMatchObject({
      slug: "nex-manager",
      name: "NEX Manager",
      backend_port: 10210,
      frontend_port: 10211,
      db_port: 10212,
      repo_url: "https://github.com/rauschiccsk/nex-manager.git",
      source_path: "/opt/projects/nex-manager",
    });
    expect(onAdopted).toHaveBeenCalledWith("nex-manager");
  });

  it("bez výberu sa prevziať nedá — inak by vzniklo prázdno", async () => {
    render(dialog());
    await screen.findByRole("option", { name: /nex-manager/ });
    expect(screen.getByRole("button", { name: /prevziať projekt/i })).toBeDisabled();
  });

  it("keď na disku nie je čo prevziať, povie to — nemlčí s prázdnym zoznamom", async () => {
    listAdoptableApiMock.mockResolvedValue([]);
    render(dialog());
    expect(await screen.findByText(/nie je nič, čo by sa dalo prevziať/i)).toBeInTheDocument();
  });
});

// ── Hláška, z ktorej sa dá niečo vyčítať (ICCINT-85) ─────────────────────────
//
// ⚠️ 09.09.2026 videl Manažér pri prevzatí NEX Managera len „Prevzatie projektu zlyhalo —
// Unprocessable Entity“ a prázdny technický detail. Server pritom napísal presnú vetu
// („Invalid repository format …“) — bola vnútri zloženej odpovede a cestou sa stratila.

import { humanizeApiError } from "@/services/apiError";
import { ApiError } from "nex-shared";

describe("humanizeApiError — veta zvnútra zloženej odpovede", () => {
  it("ukáže vetu servera, nie názov stavu", () => {
    const err = new ApiError(422, "Unprocessable Entity", {
      detail: { detail: "Invalid repository format 'https://…'. Expected 'owner/repo'.", repo_url: "x" },
    });
    expect(humanizeApiError(err, "Prevzatie projektu zlyhalo").message).toContain("Expected 'owner/repo'");
  });

  it("obyčajnú textovú odpoveď nechá tak, ako bola", () => {
    const err = new ApiError(409, "Port 10210 je už pridelený.", { detail: "Port 10210 je už pridelený." });
    expect(humanizeApiError(err, "Zlyhalo").message).toContain("Port 10210 je už pridelený.");
  });

  it("keď server nepovedal nič, ostáva zrozumiteľná náhrada — nie „[object Object]“", () => {
    const err = new ApiError(500, "[object Object]", { detail: { neco: 1 } });
    const out = humanizeApiError(err, "Zlyhalo");
    expect(out.message).not.toContain("[object Object]");
    expect(out.message.length).toBeGreaterThan("Zlyhalo".length);
  });
});
