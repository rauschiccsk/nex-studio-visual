/**
 * DEV-492 — the page of a fast fix that has not started: the tickets put into it and „Spustiť rýchlu opravu“.
 *
 * Director 22.11.2026: „Treba mať jednotný postup a to najprv verzia a potom priradzovanie tiketov.“ A fast fix is
 * founded first, takes its tickets on the „Plane tikety“ tab and starts on its page — without a ticket it does not.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { listTickets, postAction, navigate } = vi.hoisted(() => ({
  listTickets: vi.fn(),
  postAction: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("@/services/api/versions", () => ({ listVersionTicketsApi: listTickets }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: postAction }));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useNavigate: () => navigate,
}));

import FastFixTickets from "@/components/version/FastFixTickets";
import { ApiError } from "@/services/api";
import type { Version } from "@/types/version";

const FIX = { id: "v-fix", version_number: "1.1.1", status: "planned", fast_fix: true } as Version;
const TICKET = { identifier: "DEV-489", title: "Karta neukáže výsledok", url: "http://plane.test/489" };
const NO_TICKET =
  "Rýchla oprava ešte nemá zaradený žiadny tiket — zaraď ho na karte „Plane tikety“ tlačidlom „Zaradiť do verzie“.";

beforeEach(() => {
  vi.clearAllMocks();
  postAction.mockResolvedValue({});
});

describe("FastFixTickets", () => {
  it("lists the tickets put in and starts the fix from them, then opens the Riadiace centrum", async () => {
    listTickets.mockResolvedValue([TICKET]);
    render(<FastFixTickets slug="orthodox-hermenia" version={FIX} canStart />);

    expect(await screen.findByRole("link", { name: /DEV-489/ })).toHaveAttribute("href", "http://plane.test/489");
    expect(screen.getByText("Karta neukáže výsledok")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Spustiť rýchlu opravu" }));

    await waitFor(() => expect(postAction).toHaveBeenCalledWith("v-fix", { action: "start" }));
    expect(navigate).toHaveBeenCalledWith("/riadiace-centrum");
  });

  it("without a ticket it does not start and says where to put one", async () => {
    listTickets.mockResolvedValue([]);
    render(<FastFixTickets slug="orthodox-hermenia" version={FIX} canStart />);

    expect(await screen.findByText(NO_TICKET)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "Spustiť rýchlu opravu" });
    expect(start).toBeDisabled();
    expect(start).toHaveAttribute("title", NO_TICKET);
    fireEvent.click(start);
    expect(postAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Zaradiť tikety/ }));
    expect(navigate).toHaveBeenCalledWith("/projects/orthodox-hermenia/plane-tikety");
  });

  it("a refusal from the engine stays on the page in words", async () => {
    // The tickets arrive a moment later, as under load — the start is disabled until then (23.11.2026: clicked too early).
    listTickets.mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve([TICKET]), 50)));
    postAction.mockRejectedValue(new ApiError(409, "Conflict", { detail: "Stavba tejto verzie už bola spustená." }));
    render(<FastFixTickets slug="orthodox-hermenia" version={FIX} canStart />);

    expect(await screen.findByRole("link", { name: /DEV-489/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Spustiť rýchlu opravu" }));

    expect(await screen.findByText(/Stavba tejto verzie už bola spustená/)).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("someone who may not operate the project sees the tickets without the start", async () => {
    listTickets.mockResolvedValue([TICKET]);
    render(<FastFixTickets slug="orthodox-hermenia" version={FIX} canStart={false} />);

    expect(await screen.findByText("Karta neukáže výsledok")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Spustiť rýchlu opravu" })).not.toBeInTheDocument();
  });
});
