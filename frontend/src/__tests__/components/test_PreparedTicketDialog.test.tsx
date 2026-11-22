/**
 * DEV-492 — „Založiť tiket“ from a text the cockpit prepared (a change that does not work in UAT, old dead code).
 *
 * The text becomes a ticket of the project; the Manažér reads it, may edit it, picks the work kind and files it — then
 * puts it into a version or a fast fix on the „Plane tikety“ tab. Nothing starts.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { fileTicket, navigate } = vi.hoisted(() => ({ fileTicket: vi.fn(), navigate: vi.fn() }));
vi.mock("@/services/api/planeTickets", () => ({ filePreparedTicketApi: fileTicket }));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useNavigate: () => navigate,
}));

import PreparedTicketDialog from "@/components/planeTickets/PreparedTicketDialog";
import { ApiError } from "@/services/api";

const PREPARED = {
  title: "Nefunguje v UAT: Detail faktúry",
  description: "V UAT u zákazníka MÁGERSTAV vo verzii 1.8.0 nefunguje: Detail faktúry.",
  source: "uat" as const,
  workKind: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  fileTicket.mockResolvedValue({ identifier: "DEV-501", url: "http://plane.test/501" });
});

describe("PreparedTicketDialog", () => {
  it("files nothing until the work kind is chosen, then files what is in the boxes", async () => {
    render(<PreparedTicketDialog slug="nex-inbox" prepared={PREPARED} onClose={vi.fn()} />);
    const file = screen.getByRole("button", { name: "Založiť tiket" });

    expect(file).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Názov tiketu"), { target: { value: "Detail faktúry je biely" } });
    fireEvent.click(screen.getByLabelText(/Oprava chyby v dodanom kóde/));
    fireEvent.click(file);

    await waitFor(() =>
      expect(fileTicket).toHaveBeenCalledWith("nex-inbox", {
        title: "Detail faktúry je biely",
        description: PREPARED.description,
        work_kind: "fix",
        source: "uat",
      }),
    );
  });

  it("says which ticket was filed and leads to „Plane tikety“ to put it into a version", async () => {
    render(<PreparedTicketDialog slug="nex-inbox" prepared={{ ...PREPARED, workKind: "fix" }} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Založiť tiket" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Tiket DEV-501 je založený. Zaraď ho do verzie alebo do rýchlej opravy na karte „Plane tikety“.",
    );
    expect(screen.getByRole("link", { name: "DEV-501" })).toHaveAttribute("href", "http://plane.test/501");
    fireEvent.click(screen.getByRole("button", { name: "Prejsť na Plane tikety" }));
    expect(navigate).toHaveBeenCalledWith("/projects/nex-inbox/plane-tikety");
  });

  it("a refusal stays in the dialog in words and nothing is lost", async () => {
    const why = "Konto zoltan nemá zodpovedného v Plane.";
    fileTicket.mockRejectedValue(new ApiError(502, "Bad Gateway", { detail: why }));
    render(<PreparedTicketDialog slug="nex-inbox" prepared={{ ...PREPARED, workKind: "fix" }} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Založiť tiket" }));

    expect(await screen.findByText(/Založenie tiketu zlyhalo/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent(why);
    expect(screen.getByLabelText("Text tiketu")).toHaveValue(PREPARED.description);
  });
});
