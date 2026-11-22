/**
 * DEV-455, DEV-492 — from an open ticket of the „Plane tikety" tab into a version or into a fast fix, the same way.
 *
 * Director 21.11.2026: „Zásobník by sme mali teraz už úplne zrušiť a ponechať len jediný zdroj - PLANE tikety“ — the
 * ticket goes into a version, which solves it and closes it after PROD. 22.11.2026: „Treba mať jednotný postup a to
 * najprv verzia a potom priradzovanie tiketov.“ — a fast fix is founded first and takes its tickets here like a version;
 * „Spustiť rýchlu opravu“ at the ticket is gone.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { PlaneTicket } from "@/services/api/planeTickets";

const { list, putInto, takeOut } = vi.hoisted(() => ({
  list: vi.fn(),
  putInto: vi.fn(),
  takeOut: vi.fn(),
}));
vi.mock("@/services/api/planeTickets", () => ({
  listProjectPlaneTicketsApi: list,
  putTicketIntoVersionApi: putInto,
  takeTicketOutOfVersionApi: takeOut,
}));
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<typeof import("react-router-dom")>("react-router-dom")),
  useParams: () => ({ slug: "nex-inbox" }),
}));

import PlaneTicketsPage from "@/pages/PlaneTicketsPage";

function ticket(over: Partial<PlaneTicket> = {}): PlaneTicket {
  return {
    issue_id: "i114",
    identifier: "DEV-114",
    title: "Faktúra FLORASERVIS sa z ISDOC nespracuje",
    state: "Todo",
    closed: false,
    ticket_type: "Bug",
    work_kind: "fix",
    work_kind_label: "Oprava (BUG)",
    warning: null,
    doer: "Andros Dedo",
    updated_at: "2026-10-15T09:20:00Z",
    url: "http://plane.test/i/114",
    text: "Číslo faktúry je v ISDOC len vo variabilnom symbole.\n\nOver aj dobropis.",
    fix_version: null,
    in_version: null,
    ...over,
  };
}

const OPEN_VERSIONS = [
  { id: "v-190", version_number: "1.9.0", fast_fix: false },
  { id: "v-200", version_number: "2.0.0", fast_fix: false },
];

function answer(...tickets: PlaneTicket[]) {
  return { project_id: "p-uuid", project_name: "NEX Inbox", module: "NEX Inbox", tickets, open_versions: OPEN_VERSIONS };
}

async function row(identifier: string): Promise<HTMLElement> {
  const link = await screen.findByRole("link", { name: identifier });
  return link.closest("li") as HTMLElement;
}

describe("PlaneTicketsPage — from a ticket into a version (DEV-455, DEV-492)", () => {
  it("offers „Zaradiť do verzie“ at an open ticket and nothing once it went somewhere", async () => {
    list.mockResolvedValue(
      answer(
        ticket(),
        ticket({ issue_id: "i130", identifier: "DEV-130", in_version: "v1.0.0" }),
        ticket({ issue_id: "i131", identifier: "DEV-131", fix_version: "v1.9.1" }),
      ),
    );
    render(<PlaneTicketsPage />);
    const free = within(await row("DEV-114"));
    expect(free.getAllByRole("button").map((b) => b.textContent?.trim())).toEqual(["Zaradiť do verzie"]);
    expect(free.getByRole("combobox", { name: "Verzia pre tiket DEV-114" })).toHaveValue("v-200");
    const solved = within(await row("DEV-130"));
    expect(solved.getByText("Vo verzii v1.0.0")).toBeInTheDocument();
    expect(solved.queryByRole("button")).toBeNull(); // v1.0.0 is no longer open — nothing to take it out of
    const fixed = within(await row("DEV-131"));
    expect(fixed.queryByRole("button")).toBeNull();
    expect(fixed.getByText("Rýchla oprava v1.9.1")).toBeInTheDocument();
  });

  it("offers nothing at a finished ticket", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i99", identifier: "DEV-99", state: "Done", closed: true })));
    render(<PlaneTicketsPage />);
    expect(within(await row("DEV-99")).queryByRole("button")).toBeNull();
  });

  it("puts the ticket into the chosen version, says what follows and shows the list again", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i201", identifier: "DEV-201" })));
    putInto.mockResolvedValue({ issue_id: "i201", identifier: "DEV-201", version_number: "1.9.0", plane_warning: null });
    render(<PlaneTicketsPage />);
    const line = within(await row("DEV-201"));
    fireEvent.change(line.getByRole("combobox", { name: "Verzia pre tiket DEV-201" }), { target: { value: "v-190" } });
    const asked = list.mock.calls.length;
    list.mockResolvedValue(answer(ticket({ issue_id: "i201", identifier: "DEV-201", in_version: "v1.9.0" })));
    fireEvent.click(line.getByRole("button", { name: /Zaradiť do verzie/ }));
    expect(
      await screen.findByText(
        "Tiket je zaradený do verzie v1.9.0 — Príprava ho dostane a po nasadení do PROD ho kokpit uzavrie.",
      ),
    ).toBeInTheDocument();
    expect(putInto).toHaveBeenCalledWith("nex-inbox", "i201", "v-190");
    expect(await screen.findByText("Vo verzii v1.9.0")).toBeInTheDocument();
    expect(list.mock.calls.length).toBe(asked + 1);
  });

  it("says when the ticket did not move in Plane", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i202", identifier: "DEV-202" })));
    putInto.mockResolvedValue({
      issue_id: "i202",
      identifier: "DEV-202",
      version_number: "2.0.0",
      plane_warning: "Zmena je uložená, ale tiket DEV-202 v Plane sa neposunul: 503",
    });
    render(<PlaneTicketsPage />);
    fireEvent.click(within(await row("DEV-202")).getByRole("button", { name: /Zaradiť do verzie/ }));
    expect(await screen.findByText(/tiket DEV-202 v Plane sa neposunul/)).toBeInTheDocument();
  });

  it("takes a ticket out of a version that is still open", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i204", identifier: "DEV-204", in_version: "v2.0.0" })));
    takeOut.mockResolvedValue({ issue_id: "i204", identifier: "DEV-204", version_number: "2.0.0", plane_warning: null });
    render(<PlaneTicketsPage />);
    fireEvent.click(within(await row("DEV-204")).getByRole("button", { name: /Vyradiť z verzie/ }));
    expect(await screen.findByText("Tiket je vyradený z verzie v2.0.0.")).toBeInTheDocument();
    expect(takeOut).toHaveBeenCalledWith("nex-inbox", "i204", "v-200");
  });

  it("without a version to go into, says to found a version or a fast fix — and starts nothing itself", async () => {
    list.mockResolvedValue({ ...answer(ticket({ issue_id: "i205", identifier: "DEV-205" })), open_versions: [] });
    render(<PlaneTicketsPage />);
    const line = within(await row("DEV-205"));
    expect(
      line.getByText(
        "Verzia, do ktorej by sa tiket dal zaradiť, nie je — založ novú verziu alebo rýchlu opravu (Verzie → Nová verzia).",
      ),
    ).toBeInTheDocument();
    expect(line.queryAllByRole("button")).toEqual([]);
  });

  it("a fast fix waiting for its tickets is offered like a version and named so", async () => {
    list.mockResolvedValue({
      ...answer(ticket({ issue_id: "i207", identifier: "DEV-207" })),
      open_versions: [...OPEN_VERSIONS, { id: "v-201", version_number: "2.0.1", fast_fix: true }],
    });
    putInto.mockResolvedValue({ issue_id: "i207", identifier: "DEV-207", version_number: "2.0.1", plane_warning: null });
    render(<PlaneTicketsPage />);
    const line = within(await row("DEV-207"));
    const choice = line.getByRole("combobox", { name: "Verzia pre tiket DEV-207" });

    expect(within(choice).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "v1.9.0",
      "v2.0.0",
      "v2.0.1 (rýchla oprava)",
    ]);
    fireEvent.click(line.getByRole("button", { name: /Zaradiť do verzie/ }));
    await waitFor(() => expect(putInto).toHaveBeenCalledWith("nex-inbox", "i207", "v-201"));
  });
});
