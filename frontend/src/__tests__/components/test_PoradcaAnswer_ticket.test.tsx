/**
 * DEV-119 — Poradca proposes a ticket in Plane; the person founds it after a preview under the answer.
 *
 * Director 14.10.2026: „Chcel by som aby poradca vedel zakladať do plane tiket na dve veci, buď nejaký problém s NSV,
 * ten tiket potom budeš riešiť ty, alebo pre konkrétny projekt…“ Nothing is founded without the click.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import { ApiError } from "@/services/api";
import { answerForDisplay } from "@/lib/poradcaAnswer";
import type { PoradcaTicketDraft } from "@/types/poradca";

const { draftApi, foundApi } = vi.hoisted(() => ({ draftApi: vi.fn(), foundApi: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "nex-inbox" }, setSelectedVersion: vi.fn() }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  getPoradcaTicketDraftApi: draftApi,
  foundPoradcaTicketApi: foundApi,
}));

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";

const NSV_DRAFT: PoradcaTicketDraft = {
  kind: "nsv",
  title: "Tlačidlo Nasadiť pri UAT nič nerobí",
  description: "Po kliknutí sa neobjaví ani hláška.",
  ticket_type: "Bug",
  work_kind: null,
  way: null,
  responsible_options: ["Andros Dedo"],
  responsible: "Andros Dedo",
  context: ["Projekt: NEX Inbox", "Verzia: v1.9.0"],
  similar: [{ identifier: "DEV-40", title: "Tlačidlo Nasadiť pri UAT zašedne", state: "Todo", url: "http://p/40" }],
  plane_identifier: null,
  plane_url: null,
};

const PROJECT_DRAFT: PoradcaTicketDraft = {
  ...NSV_DRAFT,
  kind: "project",
  title: "Import ISDOC nečíta číslo faktúry",
  description: "Číslo je len vo VS.",
  work_kind: "fix",
  way: "fast_fix",
  responsible_options: ["Andros Dedo", "Zoltán"],
  responsible: "Zoltán",
  context: [],
  similar: [],
};

function answer(id: string, over: Record<string, unknown> = {}) {
  const message = {
    id,
    author: "poradca",
    content: "Odpoveď.",
    steps: [],
    status: "done",
    instruction: null,
    backlog_request: null,
    captured_backlog_number: null,
    choices: [],
    ticket_proposal: { kind: "nsv", title: "Tlačidlo Nasadiť pri UAT nič nerobí" },
    plane_identifier: null,
    plane_url: null,
    ...over,
  };
  return render(
    <MemoryRouter>
      <PoradcaAnswer message={message as never} scopeVersion={null} />
    </MemoryRouter>,
  );
}

async function openPanel(): Promise<ReturnType<typeof within>> {
  const opener = screen.getByRole("button", { name: /Založiť tiket/ });
  expect(opener).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(opener);
  const panel = within(screen.getByRole("region", { name: "Návrh tiketu od Poradcu" }));
  await panel.findByLabelText("Názov tiketu");
  return panel;
}

describe("A ticket from Poradca's answer (DEV-119)", () => {
  it("offers „Založiť tiket“ only under an answer that proposes one", () => {
    answer("m0", { ticket_proposal: null, backlog_request: "Pridať export faktúr do CSV." });
    expect(screen.getByRole("button", { name: /Uložiť do Zásobníka/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Založiť tiket/ })).toBeNull();
  });

  it("shows a ticket of the cockpit with what the cockpit adds and the similar open tickets", async () => {
    draftApi.mockResolvedValue(NSV_DRAFT);
    answer("m1");
    const panel = await openPanel();
    expect(draftApi).toHaveBeenCalledWith("m1");
    expect(panel.getByLabelText("Názov tiketu")).toHaveValue("Tlačidlo Nasadiť pri UAT nič nerobí");
    expect(panel.getByText(/Robí ho a zodpovedá zaň Andros Dedo/)).toBeInTheDocument();
    expect(panel.getByText("Projekt: NEX Inbox")).toBeInTheDocument();
    expect(panel.getByRole("link", { name: "DEV-40" })).toHaveAttribute("href", "http://p/40");
    expect(panel.queryByRole("radio")).toBeNull(); // no work kind and no way for the cockpit's own problem
  });

  it("founds the cockpit's ticket with the click and then names it", async () => {
    draftApi.mockResolvedValue(NSV_DRAFT);
    foundApi.mockResolvedValue({ plane_identifier: "DEV-131", plane_url: "http://p/131", created: true });
    answer("m2");
    const panel = await openPanel();
    fireEvent.click(panel.getByRole("button", { name: /Založiť tiket/ }));
    expect(await screen.findByRole("link", { name: "DEV-131" })).toHaveAttribute("href", "http://p/131");
    expect(foundApi).toHaveBeenCalledWith("m2", {
      title: "Tlačidlo Nasadiť pri UAT nič nerobí",
      description: "Po kliknutí sa neobjaví ani hláška.",
      ticket_type: "Bug",
    });
    expect(screen.queryByRole("button", { name: /Založiť tiket/ })).toBeNull();
  });

  it("starts a project ticket with Poradca's work kind, way and the person responsible", async () => {
    draftApi.mockResolvedValue(PROJECT_DRAFT);
    foundApi.mockResolvedValue({ plane_identifier: "DEV-132", plane_url: "http://p/132", created: true });
    answer("m3", { ticket_proposal: { kind: "project", title: "Import ISDOC nečíta číslo faktúry" } });
    const panel = await openPanel();
    expect(panel.getByRole("radio", { name: /Oprava/ })).toBeChecked();
    expect(panel.getByRole("radio", { name: "Do rýchlej opravy" })).toBeChecked();
    expect(panel.getByRole("combobox", { name: "Zodpovedný" })).toHaveValue("Zoltán");
    fireEvent.click(panel.getByRole("radio", { name: "Do ďalšej verzie" }));
    fireEvent.click(panel.getByRole("button", { name: /Založiť tiket/ }));
    await waitFor(() =>
      expect(foundApi).toHaveBeenCalledWith("m3", {
        title: "Import ISDOC nečíta číslo faktúry",
        description: "Číslo je len vo VS.",
        ticket_type: "Bug",
        work_kind: "fix",
        way: "version",
        responsible: "Zoltán",
      }),
    );
  });

  it("founds no project ticket until the way is chosen", async () => {
    draftApi.mockResolvedValue({ ...PROJECT_DRAFT, way: null });
    answer("m4", { ticket_proposal: { kind: "project", title: "Import" } });
    const panel = await openPanel();
    expect(panel.getByRole("button", { name: /Založiť tiket/ })).toBeDisabled();
    expect(panel.getByText(/Vyber, či tiket navrhuješ do ďalšej verzie/)).toBeInTheDocument();
  });

  it("names the ticket already founded from the answer instead of offering another", () => {
    answer("m5", { plane_identifier: "DEV-120", plane_url: "http://p/120" });
    expect(screen.getByRole("link", { name: "DEV-120" })).toHaveAttribute("href", "http://p/120");
    expect(screen.queryByRole("button", { name: /Založiť tiket/ })).toBeNull();
  });

  it("says why the ticket was not founded", async () => {
    draftApi.mockResolvedValue(NSV_DRAFT);
    foundApi.mockRejectedValue(new ApiError(502, "Plane požiadavku odmietol: 503"));
    answer("m6");
    const panel = await openPanel();
    fireEvent.click(panel.getByRole("button", { name: /Založiť tiket/ }));
    expect(await panel.findByText(/Plane požiadavku odmietol: 503/)).toBeInTheDocument();
  });

  it("shows the proposed ticket in the answer as a quote, attributes and all", () => {
    const shown = answerForDisplay(
      'Patrí do projektu.\n<tiket-projektu druh="oprava" cesta="verzia">\nImport ISDOC\nČíslo je vo VS.\n</tiket-projektu>',
    );
    expect(shown).toContain("**Návrh tiketu projektu:**\n\n> Import ISDOC\n> Číslo je vo VS.");
    expect(shown).not.toContain("<tiket-projektu");
  });
});
