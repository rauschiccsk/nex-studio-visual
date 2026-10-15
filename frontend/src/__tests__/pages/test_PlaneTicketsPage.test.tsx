/**
 * DEV-120 — the „Plane tikety" tab shows the tickets of the pinned project's module, open or finished.
 *
 * Director 14.10.2026: „Chcel by som pridať novú záložku "Plane tikety" tu by som videl tikety pre konkrétny projekt.“
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ApiError } from "@/services/api";
import type { PlaneTicket } from "@/services/api/planeTickets";

const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/services/api/planeTickets", () => ({ listProjectPlaneTicketsApi: list }));
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<typeof import("react-router-dom")>("react-router-dom")),
  useParams: () => ({ slug: "nex-inbox" }),
}));

import PlaneTicketsPage from "@/pages/PlaneTicketsPage";

function ticket(over: Partial<PlaneTicket> = {}): PlaneTicket {
  return {
    identifier: "DEV-130",
    title: "Import faktúr z ISDOC",
    state: "In Progress",
    closed: false,
    ticket_type: "Task",
    work_kind: "change",
    work_kind_label: "Požiadavka (CHR)",
    warning: null,
    doer: "Zoltán",
    updated_at: "2026-10-15T09:20:00Z",
    url: "http://plane.test/i/130",
    requirement: "REQ-7",
    requirement_version: "v1.9.0",
    ...over,
  };
}

describe("PlaneTicketsPage (DEV-120)", () => {

  it("shows the open tickets with their link, column, type, work kind, doer and requirement", async () => {
    list.mockResolvedValue({ module: "NEX Inbox", tickets: [ticket()] });
    render(<PlaneTicketsPage />);
    const link = await screen.findByRole("link", { name: "DEV-130" });
    expect(link).toHaveAttribute("href", "http://plane.test/i/130");
    expect(list).toHaveBeenCalledWith("nex-inbox", false);
    expect(screen.getByText("Import faktúr z ISDOC")).toBeInTheDocument();
    expect(screen.getByText("In Progress")).toBeInTheDocument();
    expect(screen.getByText("Požiadavka (CHR)")).toBeInTheDocument();
    expect(screen.getByText("REQ-7 · v1.9.0")).toBeInTheDocument();
    expect(screen.getByText(/Robí: Zoltán/)).toBeInTheDocument();
    expect(screen.getByText("NEX Inbox")).toBeInTheDocument();
  });

  it("asks for the finished tickets when „Uzavreté“ is chosen", async () => {
    list.mockResolvedValue({ module: "NEX Inbox", tickets: [ticket()] });
    render(<PlaneTicketsPage />);
    await screen.findByRole("link", { name: "DEV-130" });
    list.mockResolvedValue({ module: "NEX Inbox", tickets: [ticket({ identifier: "DEV-99", state: "Done", closed: true })] });
    fireEvent.click(screen.getByRole("tab", { name: "Uzavreté" }));
    await screen.findByRole("link", { name: "DEV-99" });
    expect(list).toHaveBeenLastCalledWith("nex-inbox", true);
  });

  it("says that a project without a module gets one with its first ticket", async () => {
    list.mockResolvedValue({ module: null, tickets: [] });
    render(<PlaneTicketsPage />);
    expect(await screen.findByText(/Projekt ešte nemá modul v evidencii DEV/)).toBeInTheDocument();
  });

  it("shows a ticket's label problem in words", async () => {
    list.mockResolvedValue({
      module: "NEX Inbox",
      tickets: [ticket({ work_kind: null, work_kind_label: null, warning: "Tiket nesie dva druhy práce naraz." })],
    });
    render(<PlaneTicketsPage />);
    expect(await screen.findByText("Tiket nesie dva druhy práce naraz.")).toBeInTheDocument();
  });

  it("says what failed when Plane does not answer", async () => {
    list.mockRejectedValue(new ApiError(502, "Plane na http://plane je nedostupný (ConnectError)."));
    render(<PlaneTicketsPage />);
    expect(
      await screen.findByText(/Tikety z Plane sa nepodarilo načítať — Plane na http:\/\/plane je nedostupný/),
    ).toBeInTheDocument();
  });
});
