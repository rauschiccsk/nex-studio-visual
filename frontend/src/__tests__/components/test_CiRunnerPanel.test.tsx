/**
 * DEV-158 — the repository tab says whether the project's CI runs on its own runner on ANDROS, starts a missing one with
 * one click, and names every job that runs elsewhere. NEX Inbox ran its CI on GitHub's paid machines for four months
 * (20.10.2026) because nothing in the cockpit said so. What the panel says and offers is the server's.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { ApiError } from "nex-shared";

import CiRunnerPanel, { STARTING_POLL_MS } from "@/components/repozitar/CiRunnerPanel";
import { getCiRunner, startCiRunner, type CiRunner } from "@/services/api/ciRunner";

vi.mock("@/services/api/ciRunner", () => ({ getCiRunner: vi.fn(), startCiRunner: vi.fn() }));

const LABEL = "andros-ubuntu-nex-inbox";

function runner(over: Partial<CiRunner> = {}): CiRunner {
  return {
    label: LABEL,
    repo: "rauschiccsk/nex-inbox",
    state: "none",
    github_status: null,
    starting: false,
    healthy: false,
    summary: "Projekt nemá vlastný vykonávač kontrol na ANDROSe.",
    action: "Spustiť vykonávač kontrol",
    note: null,
    jobs_elsewhere: [
      { workflow: "ci.yml", job: "lint", runs_on: "ubuntu-latest" },
      { workflow: "release-smoke.yml", job: "gate", runs_on: "ubuntu-latest" },
    ],
    unreadable_workflows: [],
    ...over,
  };
}

const STARTING = runner({
  starting: true,
  summary: "Vykonávač kontrol sa spúšťa — zvyčajne do dvoch minút; stav sa obnoví sám.",
  action: null,
});
const WORKING = runner({
  state: "current",
  github_status: "online",
  healthy: true,
  summary: `Kontroly bežia na vlastnom vykonávači ${LABEL} na ANDROSe, prihlásenom na GitHube.`,
  action: null,
  jobs_elsewhere: [],
});

function panel(slug = "nex-inbox", refreshKey = 0) {
  return render(<CiRunnerPanel slug={slug} refreshKey={refreshKey} />);
}

beforeEach(() => {
  vi.mocked(getCiRunner).mockReset().mockResolvedValue(runner());
  vi.mocked(startCiRunner).mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the project's CI runner on the repository tab (DEV-158)", () => {
  it("a project without its runner is offered one and names every job that runs elsewhere", async () => {
    panel();

    expect(await screen.findByText("Projekt nemá vlastný vykonávač kontrol na ANDROSe.")).toBeInTheDocument();
    expect(getCiRunner).toHaveBeenCalledWith("nex-inbox");
    expect(screen.getByRole("button", { name: "Spustiť vykonávač kontrol" })).toBeEnabled();
    expect(screen.getByText("Kontroly mimo vlastného vykonávača (2)")).toBeInTheDocument();
    const jobs = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(jobs).toEqual(["ci.yml › lint — ubuntu-latest", "release-smoke.yml › gate — ubuntu-latest"]);
    expect(screen.getByText(LABEL)).toBeInTheDocument();
  });

  it("a working runner offers nothing and shows no warning", async () => {
    vi.mocked(getCiRunner).mockResolvedValue(WORKING);
    panel();

    expect(await screen.findByText(WORKING.summary)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/Kontroly mimo vlastného vykonávača/)).not.toBeInTheDocument();
  });

  it("one click starts the runner, and the tab follows the start until the runner works", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(startCiRunner).mockResolvedValue(STARTING);
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Spustiť vykonávač kontrol" }));

    expect(await screen.findByText(STARTING.summary)).toBeInTheDocument();
    expect(startCiRunner).toHaveBeenCalledWith("nex-inbox");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    vi.mocked(getCiRunner).mockResolvedValue(WORKING);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STARTING_POLL_MS);
    });

    expect(screen.getByText(WORKING.summary)).toBeInTheDocument();
    const asked = vi.mocked(getCiRunner).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STARTING_POLL_MS * 3);
    });
    expect(vi.mocked(getCiRunner).mock.calls.length).toBe(asked);
  });

  it("a refused start says the server's reason and shows the runner as it is now", async () => {
    vi.mocked(startCiRunner).mockRejectedValue(
      new ApiError(409, "Vykonávač kontrol sa už spúšťa.", { detail: "Vykonávač kontrol sa už spúšťa." }),
    );
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Spustiť vykonávač kontrol" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Spustenie vykonávača kontrol zlyhalo — Vykonávač kontrol sa už spúšťa.",
    );
    await waitFor(() => expect(getCiRunner).toHaveBeenCalledTimes(2));
  });

  it("a start that did not end online says why", async () => {
    vi.mocked(getCiRunner).mockResolvedValue(
      runner({ note: "Vykonávač kontrol sa nepodarilo spustiť — kontroly počkajú, kým ho niekto spustí." }),
    );
    panel();

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Vykonávač kontrol sa nepodarilo spustiť — kontroly počkajú, kým ho niekto spustí.",
    );
  });

  it("a workflow the cockpit cannot read is named", async () => {
    vi.mocked(getCiRunner).mockResolvedValue(runner({ jobs_elsewhere: [], unreadable_workflows: ["broken.yml"] }));
    panel();

    expect(await screen.findByText("broken.yml")).toBeInTheDocument();
    expect(screen.getByText(/kokpit nevie povedať, kde bežia jeho úlohy/)).toBeInTheDocument();
  });

  it("when the runner cannot be asked about, it says so instead of showing nothing", async () => {
    vi.mocked(getCiRunner).mockRejectedValue(new Error("network"));
    panel();

    expect(await screen.findByRole("alert")).toHaveTextContent("Zistenie vykonávača kontrol zlyhalo");
  });

  it("an answer for the project selected before never lands on the one selected now", async () => {
    let answerOld: (r: CiRunner) => void = () => undefined;
    vi.mocked(getCiRunner).mockImplementation((slug) =>
      slug === "nex-inbox" ? new Promise((resolve) => (answerOld = resolve)) : Promise.resolve(WORKING),
    );
    const { rerender } = panel("nex-inbox");
    rerender(<CiRunnerPanel slug="nex-manager" refreshKey={0} />);
    expect(await screen.findByText(WORKING.summary)).toBeInTheDocument();

    await act(async () => answerOld(runner()));

    expect(screen.getByText(WORKING.summary)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("„Obnoviť“ on the tab asks again", async () => {
    const { rerender } = panel("nex-inbox", 0);
    await screen.findByText("Projekt nemá vlastný vykonávač kontrol na ANDROSe.");
    vi.mocked(getCiRunner).mockResolvedValue(WORKING);

    rerender(<CiRunnerPanel slug="nex-inbox" refreshKey={1} />);

    expect(await screen.findByText(WORKING.summary)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Vykonávač kontrol" })).queryByRole("button")).toBeNull();
  });
});
