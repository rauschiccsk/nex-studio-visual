/**
 * DEV-498 — the page of a regular version that has not started lists the tickets put into it, right where it is
 * started („Spustiť tvorbu špecifikácie“).
 *
 * 22.11.2026, Orthodox Hermenia 1.2.0: the Director was told to write the ticket numbers into the Zadanie; the Staviteľ
 * gets them by himself. „…dopisovanie čísla tiketov z mojej strany je nezmyseľná práca.“ — and: „…v okne, kde spúšťam
 * tvorbu špecifikácie, tiež nech sú zobrazené priradené tikety.“
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import VersionDetailPage from "@/pages/VersionDetailPage";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

const { listProjectsApiMock, getVersionMock, listTicketsMock, navigateMock } = vi.hoisted(() => ({
  listProjectsApiMock: vi.fn(),
  getVersionMock: vi.fn(),
  listTicketsMock: vi.fn(),
  navigateMock: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useParams: () => ({ slug: "orthodox-hermenia", versionId: "ver-120" }),
  };
});
vi.mock("@/services/api/projects", () => ({ listProjectsApi: listProjectsApiMock }));
vi.mock("@/services/api/versions", () => ({
  getVersion: getVersionMock,
  listVersionTicketsApi: listTicketsMock,
  readZadanie: vi.fn(() => Promise.resolve({ content: "" })),
  writeZadanie: vi.fn(),
  getVersionSettings: vi.fn(() => Promise.resolve({ version_number_lock_reason: null })),
  updateVersion: vi.fn(),
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: vi.fn(), setSelectedVersion: vi.fn(), selectedVersion: null }),
}));

const project = { id: "proj-h", name: "Orthodox Hermenia", slug: "orthodox-hermenia", created_by: "u1" } as ProjectRead;

const planned = {
  id: "ver-120",
  project_id: "proj-h",
  version_number: "1.2.0",
  name: null,
  status: "planned",
  fast_fix: false,
  description: null,
  target_date: null,
  release_date: null,
  created_at: "2026-11-22T20:00:00Z",
  updated_at: "2026-11-22T20:00:00Z",
  epic_count: 0,
  epics_done: 0,
} as Version;

const TICKETS = [
  { identifier: "DEV-497", title: "Prijať hotový .md bez prevodu cez Clauda", url: "http://plane.test/497" },
  { identifier: "DEV-496", title: "Webový server prijme veľký súbor", url: "http://plane.test/496" },
];

describe("VersionDetailPage — the tickets of a version that has not started (DEV-498)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    navigateMock.mockReset();
    listProjectsApiMock.mockReset().mockResolvedValue({ items: [project] });
    getVersionMock.mockReset().mockResolvedValue(planned);
    listTicketsMock.mockReset().mockResolvedValue(TICKETS);
  });

  it("lists every ticket put in, linked to Plane, beside the start of the Špecifikácia", async () => {
    render(<VersionDetailPage />);

    const section = await screen.findByRole("region", { name: "Tikety verzie" });
    expect(await within(section).findByRole("link", { name: /DEV-497/ })).toHaveAttribute("href", "http://plane.test/497");
    expect(within(section).getByText("Webový server prijme veľký súbor")).toBeInTheDocument();
    expect(listTicketsMock).toHaveBeenCalledWith("ver-120");
    expect(screen.getByRole("button", { name: "Spustiť tvorbu špecifikácie" })).toBeInTheDocument();
  });

  it("without a ticket it says where to put one and leads there — the start is not held", async () => {
    listTicketsMock.mockResolvedValue([]);
    render(<VersionDetailPage />);

    expect(
      await screen.findByText(
        "Do verzie zatiaľ nie je zaradený žiadny tiket — zaradíš ho na karte „Plane tikety“ tlačidlom „Zaradiť do verzie“.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Zaradiť tikety/ }));
    expect(navigateMock).toHaveBeenCalledWith("/projects/orthodox-hermenia/plane-tikety");
  });

  it("a version that already runs shows no ticket section", async () => {
    getVersionMock.mockResolvedValue({ ...planned, status: "active" });
    render(<VersionDetailPage />);

    expect(await screen.findByRole("button", { name: /Otvoriť v Riadiacom centre/ })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Tikety verzie" })).not.toBeInTheDocument();
  });
});
