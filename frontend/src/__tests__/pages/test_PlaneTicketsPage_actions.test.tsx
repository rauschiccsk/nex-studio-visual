/**
 * DEV-122 — from an open ticket of the „Plane tikety" tab straight to the Zásobník or to a fast fix.
 *
 * Director 14.10.2026 approved: at an open ticket „Do Zásobníka" (a linked requirement) and „Spustiť rýchlu opravu"
 * (the ticket's text is the directive, its work kind preselected, the version is created by the Manažér's click).
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError } from "@/services/api";
import type { PlaneTicket } from "@/services/api/planeTickets";

const { list, toBacklog, startFix, openCockpit } = vi.hoisted(() => ({
  list: vi.fn(),
  toBacklog: vi.fn(),
  startFix: vi.fn(),
  openCockpit: vi.fn(),
}));
vi.mock("@/services/api/planeTickets", () => ({
  listProjectPlaneTicketsApi: list,
  planeTicketToBacklogApi: toBacklog,
}));
vi.mock("@/services/api/pipeline", () => ({ startFastFixApi: startFix }));
vi.mock("@/hooks/useOpenVersionCockpit", () => ({ useOpenVersionCockpit: () => openCockpit }));
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
    requirement: null,
    requirement_version: null,
    text: "Číslo faktúry je v ISDOC len vo variabilnom symbole.\n\nOver aj dobropis.",
    fix_version: null,
    ...over,
  };
}

function answer(...tickets: PlaneTicket[]) {
  return { project_id: "p-uuid", project_name: "NEX Inbox", module: "NEX Inbox", tickets };
}

async function row(identifier: string): Promise<HTMLElement> {
  const link = await screen.findByRole("link", { name: identifier });
  return link.closest("li") as HTMLElement;
}

describe("PlaneTicketsPage — from a ticket (DEV-122)", () => {
  it("offers both ways at an open ticket and none once it went one of them", async () => {
    list.mockResolvedValue(
      answer(
        ticket(),
        ticket({ issue_id: "i130", identifier: "DEV-130", requirement: "REQ-7" }),
        ticket({ issue_id: "i131", identifier: "DEV-131", fix_version: "v1.9.1" }),
      ),
    );
    render(<PlaneTicketsPage />);
    const free = within(await row("DEV-114"));
    expect(free.getAllByRole("button").map((b) => b.textContent?.trim())).toEqual([
      "Do Zásobníka",
      "Spustiť rýchlu opravu",
    ]);
    expect(within(await row("DEV-130")).queryByRole("button")).toBeNull();
    const fixed = within(await row("DEV-131"));
    expect(fixed.queryByRole("button")).toBeNull();
    expect(fixed.getByText("Rýchla oprava v1.9.1")).toBeInTheDocument();
  });

  it("offers nothing at a finished ticket", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i99", identifier: "DEV-99", state: "Done", closed: true })));
    render(<PlaneTicketsPage />);
    expect(within(await row("DEV-99")).queryByRole("button")).toBeNull();
  });

  it("puts the ticket into the Zásobník, says its REQ-N and shows the list again", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i201", identifier: "DEV-201" })));
    toBacklog.mockResolvedValue({ id: "b1", number: 12, plane_warning: null });
    render(<PlaneTicketsPage />);
    const door = within(await row("DEV-201")).getByRole("button", { name: /Do Zásobníka/ });
    const asked = list.mock.calls.length;
    list.mockResolvedValue(answer(ticket({ issue_id: "i201", identifier: "DEV-201", requirement: "REQ-12" })));
    fireEvent.click(door);
    expect(await screen.findByText("Tiket je v Zásobníku ako REQ-12.")).toBeInTheDocument();
    expect(toBacklog).toHaveBeenCalledWith("nex-inbox", "i201");
    expect(await screen.findByText("REQ-12")).toBeInTheDocument();
    expect(list.mock.calls.length).toBe(asked + 1);
  });

  it("says when the ticket did not hear about it", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i202", identifier: "DEV-202" })));
    toBacklog.mockResolvedValue({
      id: "b2",
      number: 13,
      plane_warning: "Požiadavka REQ-13 je v Zásobníku, ale tiket DEV-202 sa o tom nedozvedel: 503",
    });
    render(<PlaneTicketsPage />);
    fireEvent.click(within(await row("DEV-202")).getByRole("button", { name: /Do Zásobníka/ }));
    expect(await screen.findByText(/tiket DEV-202 sa o tom nedozvedel/)).toBeInTheDocument();
  });

  it("opens the fast fix with the ticket's title and text as the directive and its work kind chosen", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i203", identifier: "DEV-203" })));
    startFix.mockResolvedValue({ version_id: "v-new", board: {}, plane_warning: null });
    render(<PlaneTicketsPage />);
    const opener = within(await row("DEV-203")).getByRole("button", { name: /Spustiť rýchlu opravu/ });
    expect(opener).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(opener);
    const panel = within(screen.getByRole("region", { name: "Rýchla oprava z tiketu DEV-203" }));
    expect(panel.getByLabelText("Zadanie rýchlej opravy")).toHaveValue(
      "Faktúra FLORASERVIS sa z ISDOC nespracuje\n\nČíslo faktúry je v ISDOC len vo variabilnom symbole.\n\nOver aj dobropis.",
    );
    expect(panel.getByRole("radio", { checked: true })).toHaveAttribute("value", "fix");
    fireEvent.click(panel.getByRole("button", { name: /Spustiť rýchlu opravu/ }));
    await waitFor(() =>
      expect(openCockpit).toHaveBeenCalledWith("v-new", { slug: "nex-inbox", name: "NEX Inbox" }),
    );
    expect(startFix).toHaveBeenCalledWith(
      "p-uuid",
      "Faktúra FLORASERVIS sa z ISDOC nespracuje\n\nČíslo faktúry je v ISDOC len vo variabilnom symbole.\n\nOver aj dobropis.",
      "fix",
      "i203",
    );
  });

  it("starts nothing until a work kind is chosen when the ticket has none", async () => {
    list.mockResolvedValue(
      answer(ticket({ issue_id: "i204", identifier: "DEV-204", work_kind: null, work_kind_label: null })),
    );
    render(<PlaneTicketsPage />);
    fireEvent.click(within(await row("DEV-204")).getByRole("button", { name: /Spustiť rýchlu opravu/ }));
    const panel = within(screen.getByRole("region", { name: "Rýchla oprava z tiketu DEV-204" }));
    expect(panel.getByRole("button", { name: /Spustiť rýchlu opravu/ })).toBeDisabled();
    expect(panel.getByText("Vyber druh práce.")).toBeInTheDocument();
  });

  it("keeps the sentence when Plane did not follow and leads to the fix from there", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i205", identifier: "DEV-205" })));
    startFix.mockResolvedValue({
      version_id: "v-205",
      board: {},
      plane_warning: "Rýchla oprava je spustená, ale tiket DEV-205 sa v Plane neposunul: 503",
    });
    render(<PlaneTicketsPage />);
    fireEvent.click(within(await row("DEV-205")).getByRole("button", { name: /Spustiť rýchlu opravu/ }));
    const panel = within(screen.getByRole("region", { name: "Rýchla oprava z tiketu DEV-205" }));
    fireEvent.click(panel.getByRole("button", { name: /Spustiť rýchlu opravu/ }));
    expect(await screen.findByText(/tiket DEV-205 sa v Plane neposunul/)).toBeInTheDocument();
    expect(openCockpit).not.toHaveBeenCalledWith("v-205", expect.anything());
    fireEvent.click(screen.getByRole("button", { name: /Otvoriť rýchlu opravu/ }));
    expect(openCockpit).toHaveBeenCalledWith("v-205", { slug: "nex-inbox", name: "NEX Inbox" });
  });

  it("says why the fast fix did not start", async () => {
    list.mockResolvedValue(answer(ticket({ issue_id: "i206", identifier: "DEV-206" })));
    startFix.mockRejectedValue(new ApiError(409, "Z tohto tiketu už je spustená rýchla oprava v1.9.1."));
    render(<PlaneTicketsPage />);
    fireEvent.click(within(await row("DEV-206")).getByRole("button", { name: /Spustiť rýchlu opravu/ }));
    const panel = within(screen.getByRole("region", { name: "Rýchla oprava z tiketu DEV-206" }));
    fireEvent.click(panel.getByRole("button", { name: /Spustiť rýchlu opravu/ }));
    expect(await screen.findByText(/už je spustená rýchla oprava v1\.9\.1/)).toBeInTheDocument();
  });
});
