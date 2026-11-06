/**
 * DEV-225 — the version's page has no orientačná cena of its own: it lives on the billing tab.
 *
 * Director 26.10.2026, looking at the panel on Author Asistent 0.1.0: „podľa mňa táto informácia vecne patrí skôr do
 * priečinku Fakturácia“. A version past „planned“ — where the panel used to stand — shows nothing of the estimate.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import VersionDetailPage from "@/pages/VersionDetailPage";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

const { listProjectsApiMock, getVersionMock } = vi.hoisted(() => ({
  listProjectsApiMock: vi.fn(),
  getVersionMock: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn(), useParams: () => ({ slug: "author-asistent", versionId: "ver-1" }) };
});
vi.mock("@/services/api/projects", () => ({ listProjectsApi: listProjectsApiMock }));
vi.mock("@/services/api/versions", () => ({
  getVersion: getVersionMock,
  readZadanie: vi.fn(() => Promise.resolve({ content: "Zadanie." })),
  writeZadanie: vi.fn(),
  getVersionSettings: vi.fn(() => Promise.resolve({ version_number_lock_reason: null })),
  updateVersion: vi.fn(),
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: vi.fn(), setSelectedVersion: vi.fn() }),
}));

const project = { id: "proj-1", name: "Author Asistent", slug: "author-asistent" } as ProjectRead;

const version = {
  id: "ver-1",
  project_id: "proj-1",
  version_number: "0.1.0",
  name: "Initial prototype",
  status: "active",
  description: null,
  target_date: null,
  release_date: null,
  created_at: "2026-10-24T10:00:00Z",
  updated_at: "2026-10-26T10:00:00Z",
  epic_count: 7,
  epics_done: 7,
} as Version;

describe("VersionDetailPage — no orientačná cena (DEV-225)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listProjectsApiMock.mockReset().mockResolvedValue({ items: [project] });
    getVersionMock.mockReset().mockResolvedValue(version);
  });

  it("a version past planned shows its summary and nothing of the estimate", async () => {
    render(<VersionDetailPage />);

    expect(await screen.findByRole("button", { name: /Otvoriť v Riadiacom centre/ })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Orientačná cena" })).not.toBeInTheDocument();
    expect(screen.queryByText(/rientačn/)).not.toBeInTheDocument();
  });
});
