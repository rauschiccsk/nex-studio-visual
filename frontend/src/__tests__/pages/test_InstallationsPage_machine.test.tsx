/**
 * DEV-404 — a PROD installation says the machine it was deployed to, „stroj nezaznamenaný“ for a deploy that recorded
 * none, and that the customer is to be on another machine where nothing is deployed. Director 16.11.2026: „Súhlasím,
 * založ, ale teraz nerob.“; 18.11.2026 in the batch.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const { listInstallationsApi, getInstallationStatusApi } = vi.hoisted(() => ({
  listInstallationsApi: vi.fn(),
  getInstallationStatusApi: vi.fn(),
}));
vi.mock("@/services/api/installations", () => ({ listInstallationsApi, getInstallationStatusApi }));
const { uatLaunch } = vi.hoisted(() => ({ uatLaunch: vi.fn() }));
vi.mock("@/services/api/deploy", () => ({ uatLaunch }));

import InstallationsPage from "@/pages/InstallationsPage";
import { useActiveContextStore } from "@/store/activeContextStore";

const env = (version: string, url: string, extra = {}) => ({
  version,
  url,
  links: [],
  last_attempt_failed: false,
  opens: "link",
  ...extra,
});

const GROUPS = [
  {
    key: "icc",
    name: "ICC s.r.o.",
    apps: [
      {
        customer_id: "c-icc-career",
        project_slug: "career-asistent",
        project_name: "Career Asistent",
        uat: env("1.0.0", "https://uat-icc-career-asistent.isnex.eu", { machine: "ANDROS" }),
        prod: env("1.0.0", "https://icc-career-asistent.isnex.eu", { machine: "ANDROS", planned_machine: "MAGER" }),
      },
      {
        customer_id: "c-icc-legal",
        project_slug: "nex-legal-watch",
        project_name: "NEX Legal Watch",
        uat: null,
        prod: env("0.1.0", "https://icc-legal-watch.icc.sk", { machine: null }),
      },
    ],
  },
];

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/instalacie"]}>
      <Routes>
        <Route path="/instalacie" element={<InstallationsPage />} />
        <Route path="/uat" element={<p>Stránka UAT</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  listInstallationsApi.mockReset();
  getInstallationStatusApi.mockReset();
  listInstallationsApi.mockResolvedValue(GROUPS);
  getInstallationStatusApi.mockResolvedValue({ state: "up", version: "1.0.0", detail: "" });
  useActiveContextStore.getState().setSelectedProject(null);
});

async function row(app: string): Promise<HTMLElement> {
  const icc = await screen.findByRole("region", { name: "ICC s.r.o." });
  const rows = within(icc).getAllByRole("row").slice(1);
  return rows.find((r) => within(r).getAllByRole("cell")[0]?.textContent === app) as HTMLElement;
}

describe("Inštalácie — stroj nasadenia (DEV-404)", () => {
  it("says the machine of the deploy and that the customer is to be elsewhere", async () => {
    renderPage();

    const career = await row("Career Asistent");

    expect(within(career).getByText("na stroji ANDROS")).toBeInTheDocument();
    expect(within(career).getByText("zákazník má byť na stroji MAGER — tam nenasadené")).toBeInTheDocument();
    expect(within(career).getAllByText(/na stroji ANDROS/)).toHaveLength(1);
  });

  it("says „stroj nezaznamenaný“ for a deploy that recorded none and guesses nothing", async () => {
    renderPage();

    const legal = await row("NEX Legal Watch");

    expect(within(legal).getByText("stroj nezaznamenaný")).toBeInTheDocument();
    expect(within(legal).queryByText(/zákazník má byť/)).toBeNull();
  });
});
