/**
 * DEV-84 — the repository tab shows the selected project's CI runs as GitHub does: the state in words, the failed step,
 * the task a commit belongs to (a link into the task plan) and where the commit runs; filtered, searched and refreshed
 * on the server's schedule.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import RepozitarPage from "@/pages/RepozitarPage";
import { getCiRuns, type CiRun, type CiRuns } from "@/services/api/ciRuns";

vi.mock("@/services/api/ciRuns", () => ({ getCiRuns: vi.fn() }));

const context = {
  selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } as { slug: string; name: string } | null,
  setSelectedVersion: vi.fn(),
  setSelectedProject: vi.fn(),
};
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof context) => unknown) => selector(context),
}));

function run(id: number, over: Partial<CiRun> = {}): CiRun {
  return {
    id,
    number: id,
    attempt: 1,
    workflow: "CI",
    subject: `feat: run ${id}`,
    event: "push",
    event_label: "odoslanie",
    state: "success",
    state_label: "Prešlo",
    branch: "main",
    sha: `${id}`.padStart(40, "a"),
    actor: "rauschiccsk",
    created_at: "2026-10-12T20:42:08Z",
    duration_seconds: 716,
    url: `https://github.com/rauschiccsk/nex-inbox/actions/runs/${id}`,
    failed_step: null,
    task: null,
    deployed: [],
    ...over,
  };
}

const RED = run(25, {
  subject: "proof(red): nginx without the .mjs type (do not merge)",
  state: "failure",
  state_label: "Zlyhalo",
  branch: "ci/v180-gate-red-mjs",
  event_label: "spustené ručne",
  actor: "tibor",
  failed_step: "gate › Brána vydania (release_smoke_test.sh)",
});
const TASKED = run(24, {
  subject: "test(1.8.0): release gate (TASK #32.2.1)",
  task: { label: "32.2.1", task_id: "t", title: "Brána vydania", version_id: "v180", version_number: "1.8.0" },
  deployed: [{ environment: "prod", customer: "MÁGERSTAV", version: "1.7.1" }],
});

function answer(over: Partial<CiRuns> = {}): CiRuns {
  return {
    project_name: "NEX Inbox",
    repo: "rauschiccsk/nex-inbox",
    actions_url: "https://github.com/rauschiccsk/nex-inbox/actions",
    total_count: 193,
    fetched_at: "2026-10-12T20:55:00Z",
    error: null,
    stale: false,
    refresh_seconds: 30,
    runs: [RED, TASKED],
    ...over,
  };
}

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function page(address = "/repozitar") {
  return render(
    <MemoryRouter initialEntries={[address]}>
      <Routes>
        <Route
          path="/repozitar"
          element={
            <>
              <RepozitarPage />
              <Where />
            </>
          }
        />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  context.selectedProject = { slug: "nex-inbox", name: "NEX Inbox" };
  context.setSelectedVersion.mockReset();
  context.setSelectedProject.mockReset();
  vi.mocked(getCiRuns).mockReset().mockResolvedValue(answer());
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the repository tab (DEV-84)", () => {
  it("without a project asks for one and never asks for runs", () => {
    context.selectedProject = null;
    page();

    expect(screen.getByText("Nemáš vybraný projekt")).toBeInTheDocument();
    expect(getCiRuns).not.toHaveBeenCalled();
  });

  it("shows each run with its state in words, the step that failed and where its commit runs", async () => {
    page();

    const runs = await screen.findAllByRole("listitem");
    expect(getCiRuns).toHaveBeenCalledWith("nex-inbox");
    const red = within(runs[0]!);
    expect(red.getByText("proof(red): nginx without the .mjs type (do not merge)")).toBeInTheDocument();
    expect(red.getByText("Zlyhalo")).toBeInTheDocument();
    expect(red.getByText("Zlyhal krok: gate › Brána vydania (release_smoke_test.sh)")).toBeInTheDocument();
    expect(red.getByRole("link", { name: "Beh #25 na GitHube" })).toHaveAttribute("href", RED.url);
    const tasked = within(runs[1]!);
    expect(tasked.getByText("na PROD · MÁGERSTAV · 1.7.1")).toBeInTheDocument();
    expect(tasked.getByRole("button", { name: "Úloha 32.2.1 · verzia 1.8.0" })).toBeInTheDocument();
    expect(screen.getByText(/Behy: 2 zobrazené z 2 najnovších · GitHub ich má spolu 193\./)).toBeInTheDocument();
  });

  it("a task opens its version's task plan at the task", async () => {
    page();

    fireEvent.click(await screen.findByRole("button", { name: "Úloha 32.2.1 · verzia 1.8.0" }));

    expect(context.setSelectedVersion).toHaveBeenCalledWith({ versionId: "v180", versionNumber: "1.8.0" });
    expect(screen.getByTestId("where")).toHaveTextContent("/riadiace-centrum?uloha=32.2.1");
  });

  it("filters by state, branch and person, and searches the task number", async () => {
    page();
    await screen.findAllByRole("listitem");
    const shown = () => screen.getAllByRole("listitem").map((li) => li.getAttribute("data-run"));

    fireEvent.change(screen.getByLabelText("Stav"), { target: { value: "failure" } });
    expect(shown()).toEqual(["25"]);
    fireEvent.change(screen.getByLabelText("Stav"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Kto"), { target: { value: "tibor" } });
    expect(shown()).toEqual(["25"]);
    fireEvent.change(screen.getByLabelText("Kto"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Vetva"), { target: { value: "main" } });
    expect(shown()).toEqual(["24"]);
    fireEvent.change(screen.getByLabelText("Vetva"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Hľadať v behoch"), { target: { value: "32.2.1" } });
    expect(shown()).toEqual(["24"]);
    fireEvent.change(screen.getByLabelText("Hľadať v behoch"), { target: { value: "nič také" } });
    expect(screen.getByText("Žiadny beh nezodpovedá výberu.")).toBeInTheDocument();
  });

  it("old runs from a GitHub that stopped answering say so and from when", async () => {
    vi.mocked(getCiRuns).mockResolvedValue(answer({ error: "GitHub neodpovedal", stale: true }));
    page();

    const note = await screen.findByRole("status");
    expect(note.textContent).toMatch(/^GitHub sa teraz nedá opýtať \(GitHub neodpovedal\) — zobrazené behy sú z .+\.$/);
  });

  it("a link from Telegram opens the project it names and makes it the selected one (DEV-85)", async () => {
    context.selectedProject = null;
    // The real store keeps what it is given — the page then reads the project from it, not from the address.
    context.setSelectedProject.mockImplementation((p: { slug: string; name: string }) => {
      context.selectedProject = p;
    });
    vi.mocked(getCiRuns).mockResolvedValue(answer({ project_name: "NEX Manager" }));
    page("/repozitar?projekt=nex-manager");

    await screen.findAllByRole("listitem");
    expect(getCiRuns).toHaveBeenCalledWith("nex-manager");
    expect(context.setSelectedProject).toHaveBeenCalledWith({ slug: "nex-manager", name: "NEX Manager" });
    // The router changes the address in a transition — it may land after the list is drawn, so it is waited for.
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/repozitar$/));
    expect(screen.getByText("NEX Manager")).toBeInTheDocument();
  });

  it("asks again on the server's schedule, not its own", async () => {
    vi.useFakeTimers();
    vi.mocked(getCiRuns).mockResolvedValue(answer({ refresh_seconds: 45 }));
    page();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const after = () => vi.mocked(getCiRuns).mock.calls.length;
    const first = after();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(after()).toBe(first);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(after()).toBe(first + 1);
  });
});
