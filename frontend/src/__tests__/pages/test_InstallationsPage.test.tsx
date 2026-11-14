/**
 * DEV-378 — „Inštalácie“: every installation the cockpit deployed, by customer, UAT and PROD side by side.
 *
 * 13.11.2026 the Director lost track of many alike icons (NEX Manager for ICC and MÁGERSTAV, each on UAT and PROD) and
 * asked for something that stays clear with 50+ customers. On the page as explained: „Súhlasím, ideme to robiť.“
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const { listInstallationsApi, getInstallationStatusApi } = vi.hoisted(() => ({
  listInstallationsApi: vi.fn(),
  getInstallationStatusApi: vi.fn(),
}));
vi.mock("@/services/api/installations", () => ({ listInstallationsApi, getInstallationStatusApi }));

import InstallationsPage from "@/pages/InstallationsPage";
import { SETTINGS } from "@/config/settings";
import { useActiveContextStore } from "@/store/activeContextStore";

const env = (version: string, url: string, extra = {}) => ({ version, url, links: [], last_attempt_failed: false, ...extra });

const GROUPS = [
  {
    key: "icc",
    name: "ICC s.r.o.",
    apps: [
      {
        customer_id: "c-icc-nm",
        project_slug: "nex-manager",
        project_name: "NEX Manager",
        uat: env("1.6.0", "https://uat-icc-manager.isnex.eu"),
        prod: env("1.6.0", "https://icc-manager.icc.sk"),
      },
      {
        customer_id: "c-icc-lw",
        project_slug: "nex-legal-watch",
        project_name: "NEX Legal Watch",
        uat: env("0.1.0", "https://uat-icc-legal-watch.isnex.eu", { last_attempt_failed: true }),
        prod: null,
      },
    ],
  },
  {
    key: "mager",
    name: "MÁGERSTAV s.r.o.",
    apps: [
      {
        customer_id: "c-mager-nm",
        project_slug: "nex-manager",
        project_name: "NEX Manager",
        uat: env("1.6.0", "https://uat-mager-manager.isnex.eu"),
        prod: env("1.6.0", "https://mager-manager.icc.sk", {
          links: [
            { label: "Administrácia", url: "https://mager-manager.icc.sk" },
            { label: "Web", url: "https://mager-manager-web.icc.sk" },
          ],
        }),
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

function section(name: string): HTMLElement {
  return screen.getByRole("region", { name });
}

beforeEach(() => {
  listInstallationsApi.mockReset();
  getInstallationStatusApi.mockReset();
  listInstallationsApi.mockResolvedValue(GROUPS);
  getInstallationStatusApi.mockResolvedValue({ state: "up", version: "1.6.0", detail: "" });
  useActiveContextStore.getState().setSelectedProject(null);
});

describe("Inštalácie (DEV-378)", () => {
  it("shows every customer with its apps, UAT and PROD side by side, and a dash where nothing is deployed", async () => {
    renderPage();

    const icc = await screen.findByRole("region", { name: "ICC s.r.o." });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "ICC s.r.o.",
      "MÁGERSTAV s.r.o.",
    ]);
    const rows = within(icc).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]?.textContent)).toEqual(["NEX Manager", "NEX Legal Watch"]);
    const legalWatch = rows[1]!;
    expect(within(legalWatch).getByText("— ešte nenasadené")).toBeInTheDocument();
    expect(within(legalWatch).getByText("posledný pokus o nasadenie zlyhal")).toBeInTheDocument();
  });

  it("„Otvoriť“ opens the installation in a new window — every frontend of a Web project by its own name", async () => {
    renderPage();
    await screen.findByRole("region", { name: "ICC s.r.o." });

    const open = within(section("ICC s.r.o.")).getAllByRole("link", { name: "Otvoriť" })[0];
    expect(open).toHaveAttribute("href", "https://uat-icc-manager.isnex.eu");
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    const mager = section("MÁGERSTAV s.r.o.");
    expect(within(mager).getByRole("link", { name: "Otvoriť: Web" })).toHaveAttribute(
      "href",
      "https://mager-manager-web.icc.sk",
    );
  });

  it.each([
    [{ state: "up", version: "1.6.0", detail: "" }, "● beží"],
    [{ state: "answers", version: null, detail: "aplikácia odpovedá, stav nehlási" }, "◐ odpovedá"],
    [{ state: "down", version: null, detail: "certifikát nie je platný" }, "○ neodpovedá — certifikát nie je platný"],
  ])("says what the installation answered: %o", async (answer, text) => {
    getInstallationStatusApi.mockResolvedValue(answer);
    renderPage();

    await waitFor(() => expect(screen.getAllByText(text).length).toBe(5));
  });

  it("asks the installations a few at a time, never all at once", async () => {
    let inFlight = 0;
    let most = 0;
    const waiting: (() => void)[] = [];
    getInstallationStatusApi.mockImplementation(
      () =>
        new Promise((resolve) => {
          inFlight += 1;
          most = Math.max(most, inFlight);
          waiting.push(() => {
            inFlight -= 1;
            resolve({ state: "up", version: null, detail: "" });
          });
        }),
    );
    renderPage();
    await screen.findByRole("region", { name: "ICC s.r.o." });

    while (getInstallationStatusApi.mock.calls.length < 5 || waiting.length) {
      await waitFor(() => expect(waiting.length).toBeGreaterThan(0));
      await act(async () => waiting.shift()!());
    }
    expect(getInstallationStatusApi.mock.calls.map((c) => c.join(":")).sort()).toEqual(
      ["c-icc-lw:uat", "c-icc-nm:prod", "c-icc-nm:uat", "c-mager-nm:prod", "c-mager-nm:uat"],
    );
    expect(most).toBe(SETTINGS.INSTALLATION_STATUS_CONCURRENCY);
  });

  it("the search finds a customer without its accents, and an app by its name", async () => {
    renderPage();
    await screen.findByRole("region", { name: "ICC s.r.o." });

    fireEvent.change(screen.getByLabelText("Hľadať zákazníka alebo aplikáciu"), { target: { value: "mager" } });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["MÁGERSTAV s.r.o."]);

    fireEvent.change(screen.getByLabelText("Hľadať zákazníka alebo aplikáciu"), { target: { value: "legal" } });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["ICC s.r.o."]);
    expect(within(section("ICC s.r.o.")).getAllByRole("row")).toHaveLength(2);
  });

  it("the environment filter keeps only that column and the apps deployed there; the app filter one app", async () => {
    renderPage();
    await screen.findByRole("region", { name: "ICC s.r.o." });

    fireEvent.change(screen.getByLabelText(/Prostredie/), { target: { value: "prod" } });
    const icc = section("ICC s.r.o.");
    expect(within(icc).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Aplikácia", "PROD"]);
    expect(within(icc).queryByText("NEX Legal Watch")).toBeNull();

    fireEvent.change(screen.getByLabelText(/Prostredie/), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText(/Aplikácia/), { target: { value: "NEX Legal Watch" } });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["ICC s.r.o."]);
  });

  it("the app's name leads to its deploy page, with its project pinned", async () => {
    renderPage();
    await screen.findByRole("region", { name: "ICC s.r.o." });

    fireEvent.click(within(section("MÁGERSTAV s.r.o.")).getByRole("button", { name: "NEX Manager" }));

    expect(await screen.findByText("Stránka UAT")).toBeInTheDocument();
    expect(useActiveContextStore.getState().selectedProject).toEqual({ slug: "nex-manager", name: "NEX Manager" });
  });

  it("says so when the cockpit has deployed nothing yet", async () => {
    listInstallationsApi.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText("Kokpit zatiaľ nenasadil žiadnu inštaláciu.")).toBeInTheDocument();
  });
});
