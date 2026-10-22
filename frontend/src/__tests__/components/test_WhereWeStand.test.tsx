/**
 * DEV-150 — „Kde stojíme“ on the Prehľad says where each project stands, who is on turn since when, what it takes to
 * carry on, and „Pokračovať“ opens the version in Riadiace centrum. A failed load is not „nothing stands“.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import WhereWeStand from "@/components/dashboard/WhereWeStand";
import { daysWords, zOrZo } from "@/components/dashboard/standingWords";
import * as standingApi from "@/services/api/standing";
import * as versionsApi from "@/services/api/versions";

vi.mock("@/services/api/standing", () => ({ getStandingApi: vi.fn() }));
vi.mock("@/services/api/versions", () => ({ getVersion: vi.fn() }));

const open = (over: Partial<standingApi.OpenVersionStanding>): standingApi.OpenVersionStanding => ({
  version_id: "v-1",
  version_number: "1.0.0",
  stage: "programovanie",
  status: "agent_working",
  block_reason: null,
  waiting_since: null,
  tasks_done: 0,
  tasks_total: 0,
  fast_fix: false,
  ...over,
});

const STANDING: standingApi.Standing = {
  projects: [
    {
      project_id: "p-1",
      name: "NEX Manager",
      slug: "nex-manager",
      prod_version: "1.2.2",
      open_versions: [
        open({
          version_id: "v-130",
          version_number: "1.3.0",
          status: "awaiting_manazer",
          waiting_since: new Date(2026, 9, 15, 9).toISOString(),
          tasks_done: 7,
          tasks_total: 16,
        }),
      ],
    },
    {
      project_id: "p-2",
      name: "NEX Inbox",
      slug: "nex-inbox",
      prod_version: null,
      open_versions: [
        open({
          version_id: "v-110",
          version_number: "1.10.0",
          status: "blocked",
          block_reason: "agent_question",
          waiting_since: new Date(2026, 9, 21, 9).toISOString(),
        }),
      ],
    },
    { project_id: "p-3", name: "Career Asistent", slug: "career-asistent", prod_version: null, open_versions: [open({ version_number: "1.2.0", tasks_done: 2, tasks_total: 5 })] },
    { project_id: "p-4", name: "NEX Genesis", slug: "nex-genesis", prod_version: "1.2.2", open_versions: [] },
  ],
};

function renderIt() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<WhereWeStand />} />
        <Route path="/vyvoj" element={<div>RIADIACE CENTRUM</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WhereWeStand", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 22, 12));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("says for each project where it stands, who is on turn since when and what it takes to carry on", async () => {
    vi.mocked(standingApi.getStandingApi).mockResolvedValue(STANDING);
    renderIt();

    expect(await screen.findByText("NEX Manager 1.3.0 — Programovanie, hotových 7 zo 16 úloh")).toBeInTheDocument();
    expect(screen.getByText("Čaká na teba od 15.10. (7 dní)")).toBeInTheDocument();
    expect(
      screen.getByText("Čaká na tvoje schválenie fázy Programovanie v Riadiacom centre. Na PROD je 1.2.2."),
    ).toBeInTheDocument();
    expect(screen.getByText("NEX Inbox 1.10.0 — Programovanie")).toBeInTheDocument();
    expect(screen.getByText("Čaká na teba od 21.10. (1 deň)")).toBeInTheDocument();
    expect(
      screen.getByText("Agent sa pýta — pokračuje sa v Riadiacom centre. Na PROD zatiaľ nie je nič."),
    ).toBeInTheDocument();
    expect(screen.getByText("Agent pracuje")).toBeInTheDocument();
    expect(screen.getByText("Nič nestojí. Na PROD je 1.2.2.")).toBeInTheDocument();
  });

  it("„Pokračovať“ opens the version in Riadiace centrum", async () => {
    vi.mocked(standingApi.getStandingApi).mockResolvedValue(STANDING);
    vi.mocked(versionsApi.getVersion).mockResolvedValue({ id: "v-130", version_number: "1.3.0" } as never);
    renderIt();

    const buttons = await screen.findAllByRole("button", { name: "Pokračovať" });
    fireEvent.click(buttons[0]!);

    expect(await screen.findByText("RIADIACE CENTRUM")).toBeInTheDocument();
    expect(versionsApi.getVersion).toHaveBeenCalledWith("v-130");
  });

  it("a failed load says so — it is not „nothing stands“", async () => {
    vi.mocked(standingApi.getStandingApi).mockRejectedValue(new Error("boom"));
    renderIt();

    await waitFor(() => expect(screen.getByText(/Stav projektov sa nepodarilo načítať/)).toBeInTheDocument());
    expect(screen.queryByText(/Nič nestojí/)).not.toBeInTheDocument();
  });

  it("says „z“ or „zo“ before a number as it is read", () => {
    expect([2, 4, 5, 6, 7, 14, 15, 16, 17, 20, 40, 60, 75, 100, 150, 200, 400].map((n) => `${zOrZo(n)} ${n}`)).toEqual([
      "z 2",
      "zo 4",
      "z 5",
      "zo 6",
      "zo 7",
      "zo 14",
      "z 15",
      "zo 16",
      "zo 17",
      "z 20",
      "zo 40",
      "zo 60",
      "zo 75",
      "zo 100",
      "zo 150",
      "z 200",
      "zo 400",
    ]);
  });

  it("counts the days in Slovak", () => {
    const now = new Date(2026, 9, 22, 12).getTime();
    expect([0, 1, 2, 4, 5, 11].map((d) => daysWords(new Date(now - d * 86400000).toISOString(), now))).toEqual([
      "dnes",
      "1 deň",
      "2 dni",
      "4 dni",
      "5 dní",
      "11 dní",
    ]);
  });
});
