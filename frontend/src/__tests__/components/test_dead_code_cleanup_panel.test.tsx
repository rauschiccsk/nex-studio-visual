/**
 * DEV-156 — the project page offers the cleanup of the code nobody uses that earlier versions left behind: the
 * findings, and one click that puts the prepared fast fix into the „Rýchla oprava“ dialog. Nothing starts by itself.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("@/services/api/deadCode", () => ({ getDeadCodeApi: vi.fn() }));

import DeadCodeCleanupPanel from "@/components/projects/DeadCodeCleanupPanel";
import { getDeadCodeApi, type DeadCode } from "@/services/api/deadCode";

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

function renderPanel(found: DeadCode, onCleanUp?: (brief: string) => void) {
  vi.mocked(getDeadCodeApi).mockResolvedValue(found);
  render(<DeadCodeCleanupPanel slug="nex-inbox" onCleanUp={onCleanUp} />);
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

  it("one click hands the prepared fast fix to the page — the page opens its dialog (the page's own test)", async () => {
    const onCleanUp = vi.fn();
    renderPanel(
      { findings: [finding(1)], not_run: [], brief: BRIEF },
      onCleanUp,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Upratať nepoužívaný kód" }),
    );

    expect(onCleanUp).toHaveBeenCalledWith(BRIEF);
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
