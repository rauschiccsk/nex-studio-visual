/**
 * DEV-156 — the project page offers the cleanup of the code nobody uses that earlier versions left behind: the
 * findings, and one click that prepares a ticket of the project from the check's text (DEV-492). Nothing starts.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("@/services/api/deadCode", () => ({ getDeadCodeApi: vi.fn() }));
vi.mock("@/services/api/planeTickets", () => ({ filePreparedTicketApi: vi.fn() }));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useNavigate: () => vi.fn(),
}));

import DeadCodeCleanupPanel from "@/components/projects/DeadCodeCleanupPanel";
import { getDeadCodeApi, type DeadCode } from "@/services/api/deadCode";
import { filePreparedTicketApi } from "@/services/api/planeTickets";

function finding(n: number) {
  return {
    kind: "function",
    path: "backend/app/x.py",
    name: `f${n}`,
    line: n,
    sentence: `funkciu f${n} (backend/app/x.py:${n}) nikto nevolá`,
  };
}

const BRIEF = "Uprac kód, ktorý nikto nepoužíva — …";

function renderPanel(found: DeadCode, canFile = false) {
  vi.mocked(getDeadCodeApi).mockResolvedValue(found);
  render(<DeadCodeCleanupPanel slug="nex-inbox" canFile={canFile} />);
}

afterEach(() => vi.clearAllMocks());

describe("the cleanup of the code nobody uses", () => {
  it("lists what the check found, five first, and all on request", async () => {
    renderPanel({
      findings: [1, 2, 3, 4, 5, 6, 7].map(finding),
      not_run: [],
      brief: BRIEF,
    });

    expect(
      await screen.findByText(/našla v projekte 7 vecí, ktoré nikto/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(
      [1, 2, 3, 4, 5].map((n) => finding(n).sentence),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Zobraziť všetky (7)" }),
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(7);
  });

  it("one click prepares a ticket of the project from the check's text — a fix, a version or nothing, he decides", async () => {
    renderPanel({ findings: [finding(1)], not_run: [], brief: BRIEF }, true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Upratať nepoužívaný kód" }),
    );

    expect(screen.getByRole("dialog", { name: "Založiť tiket" })).toBeInTheDocument();
    expect(screen.getByLabelText("Názov tiketu")).toHaveValue("Upratať nepoužívaný kód z minulých verzií");
    expect(screen.getByLabelText("Text tiketu")).toHaveValue(BRIEF);
    expect(filePreparedTicketApi).not.toHaveBeenCalled();
  });

  it("someone who may not operate the project reads the findings without the button", async () => {
    renderPanel({ findings: [finding(1)], not_run: [], brief: BRIEF });

    expect(await screen.findByText(finding(1).sentence)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Upratať nepoužívaný kód" }),
    ).not.toBeInTheDocument();
  });

  it("a check that did not run says why", async () => {
    renderPanel({
      findings: [],
      not_run: ["projekt nemá čitateľný repozitár v gite"],
      brief: null,
    });

    expect(
      await screen.findByText(
        "Kontrola nepoužívaného kódu neprebehla celá: projekt nemá čitateľný repozitár v gite.",
      ),
    ).toBeInTheDocument();
  });

  it("a clean project shows nothing", async () => {
    renderPanel({ findings: [], not_run: [], brief: null });

    await vi.waitFor(() => expect(getDeadCodeApi).toHaveBeenCalled());
    expect(
      screen.queryByRole("region", {
        name: "Nepoužívaný kód z minulých verzií",
      }),
    ).not.toBeInTheDocument();
  });
});
