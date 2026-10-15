/**
 * DEV-121 — a requirement of the Zásobník founds its ticket in Plane, shows it, and a rejection carries its reason.
 *
 * Director 14.10.2026: from a requirement a person founds a ticket (a draft to edit: title, description, work kind,
 * type, who is responsible); the requirement and the ticket stay linked, and the ticket follows the requirement on
 * its own — rejected with the reason. When Plane does not follow, the change is saved and a sentence says so.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { BacklogItemRead } from "@/types/backlog";

const { list, update, draft, create } = vi.hoisted(() => ({
  list: vi.fn(),
  update: vi.fn(),
  draft: vi.fn(),
  create: vi.fn(),
}));

vi.mock("@/services/api/backlog", () => ({
  listBacklogApi: list,
  updateBacklogApi: update,
  getBacklogTicketDraftApi: draft,
  createBacklogTicketApi: create,
  createBacklogApi: vi.fn(),
  deleteBacklogApi: vi.fn(),
}));
vi.mock("@/services/api/projects", () => ({
  listProjectsApi: vi.fn().mockResolvedValue({ items: [{ id: "p1", slug: "nex-inbox", name: "NEX Inbox" }] }),
}));
vi.mock("@/services/api/versions", () => ({ listVersions: vi.fn().mockResolvedValue([]) }));
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<typeof import("react-router-dom")>("react-router-dom")),
  useParams: () => ({ slug: "nex-inbox" }),
}));

import BacklogPage from "@/pages/BacklogPage";

function item(number: number, over: Partial<BacklogItemRead> = {}): BacklogItemRead {
  return {
    id: `r${number}`,
    project_id: "p1",
    number,
    title: `Požiadavka ${number}`,
    description: null,
    priority: "medium",
    status: "open",
    version_id: null,
    realized_at: null,
    rejection_reason: null,
    plane_identifier: null,
    plane_url: null,
    created_at: "2026-10-15T08:00:00Z",
    updated_at: "2026-10-15T08:00:00Z",
    ...over,
  };
}

/** The row of one requirement — the nearest ancestor naming exactly one REQ that also holds its buttons. */
function rowOf(text: string): HTMLElement {
  let node: HTMLElement | null = screen.getByText(text);
  while (node && !((node.textContent ?? "").match(/REQ-\d+/g)?.length === 1 && node.querySelector("button")))
    node = node.parentElement;
  return node as HTMLElement;
}

async function renderPage(items: BacklogItemRead[]) {
  list.mockResolvedValue({ items, total: items.length });
  render(<BacklogPage />);
  await screen.findByText(items[0]!.title);
}

describe("BacklogPage — the requirement's ticket (DEV-121)", () => {
  beforeEach(() => {
    list.mockReset();
    update.mockReset();
    draft.mockReset().mockResolvedValue({
      title: "Požiadavka 1",
      description: "",
      ticket_type: "Task",
      responsible_options: ["Andros Dedo", "Zoltán"],
      responsible: "Zoltán",
      plane_identifier: null,
      plane_url: null,
    });
    create.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it("founds a ticket from the edited draft only once a work kind is chosen", async () => {
    await renderPage([item(1)]);
    fireEvent.click(within(rowOf("Požiadavka 1")).getByRole("button", { name: "Založiť tiket" }));
    const panel = await screen.findByRole("region", { name: "Tiket pre REQ-1" });
    const submit = await within(panel).findByRole("button", { name: "Založiť tiket" });
    expect(submit).toBeDisabled();
    expect(within(panel).getByRole("combobox", { name: "Zodpovedný" })).toHaveValue("Zoltán");
    fireEvent.change(within(panel).getByRole("textbox", { name: "Názov tiketu" }), {
      target: { value: "Import faktúr z ISDOC" },
    });
    fireEvent.click(within(panel).getByRole("radio", { name: /Zmena alebo nová práca/ }));
    create.mockResolvedValue(item(1, { plane_identifier: "DEV-130", plane_url: "http://plane/i/130" }));
    list.mockResolvedValue({ items: [item(1, { plane_identifier: "DEV-130", plane_url: "http://plane/i/130" })] });
    fireEvent.click(submit);
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("r1", {
        title: "Import faktúr z ISDOC",
        description: "",
        work_kind: "change",
        ticket_type: "Task",
        responsible: "Zoltán",
      }),
    );
    const link = await screen.findByRole("link", { name: "DEV-130" });
    expect(link).toHaveAttribute("href", "http://plane/i/130");
    expect(screen.queryByRole("region", { name: "Tiket pre REQ-1" })).toBeNull();
  });

  it("shows the ticket of a linked requirement and offers no second one", async () => {
    await renderPage([item(1, { plane_identifier: "DEV-7", plane_url: "http://plane/i/7" })]);
    expect(screen.getByRole("link", { name: "DEV-7" })).toHaveAttribute("href", "http://plane/i/7");
    expect(within(rowOf("Požiadavka 1")).queryByRole("button", { name: "Založiť tiket" })).toBeNull();
  });

  it("offers no ticket for a rejected requirement and shows why it was rejected", async () => {
    await renderPage([item(1, { status: "rejected", rejection_reason: "Robí to už ERP." })]);
    expect(within(rowOf("Požiadavka 1")).queryByRole("button", { name: "Založiť tiket" })).toBeNull();
    expect(screen.getByText("Dôvod zamietnutia: Robí to už ERP.")).toBeInTheDocument();
  });

  it("rejects only with a reason and sends it along", async () => {
    await renderPage([item(1, { plane_identifier: "DEV-7", plane_url: "http://plane/i/7" })]);
    fireEvent.click(within(rowOf("Požiadavka 1")).getByRole("button", { name: "Zamietnuť" }));
    const reason = screen.getByRole("textbox", { name: "Dôvod zamietnutia" });
    expect(reason).toHaveAttribute("placeholder", expect.stringContaining("DEV-7"));
    // The row's „Zamietnuť“ opened the form; the form's own „Zamietnuť“ confirms.
    const confirm = screen.getAllByRole("button", { name: "Zamietnuť" }).at(-1)!;
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: "Robí to už ERP." } });
    update.mockResolvedValue(item(1, { status: "rejected", rejection_reason: "Robí to už ERP." }));
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("r1", { status: "rejected", rejection_reason: "Robí to už ERP." }),
    );
  });

  it("says so when the change is saved but the ticket did not move in Plane", async () => {
    await renderPage([item(1, { status: "rejected", rejection_reason: "x", plane_identifier: "DEV-7" })]);
    update.mockResolvedValue({
      ...item(1, { plane_identifier: "DEV-7" }),
      plane_warning: "Požiadavka je uložená, ale tiket DEV-7 v Plane sa neposunul: Plane je nedostupný.",
    });
    fireEvent.click(within(rowOf("Požiadavka 1")).getByRole("button", { name: "Vrátiť" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Požiadavka je uložená, ale tiket DEV-7 v Plane sa neposunul: Plane je nedostupný.",
    );
  });
});
