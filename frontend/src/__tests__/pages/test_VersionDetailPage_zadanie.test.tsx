/**
 * VersionDetailPage — an UNREADABLE Zadanie must not look like an empty one.
 *
 * The editor loaded the brief with `readZadanie(...).catch(() => ({ content: "" }))`. The backend already
 * answers "" for a file that does not exist yet, so that catch fired ONLY on real failures — 403 (a Junior on
 * someone else's project), 404, 500, a network drop. Every one of them became an empty editor
 * indistinguishable from "nothing written yet", and pressing "Uložiť Zadanie" then truncated the real file.
 *
 * These pin the third state: the failure is visible, the editor is locked, and neither Uložiť nor Spustiť can
 * act on content nobody has seen — plus the normal read/save path is unchanged.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import VersionDetailPage from "@/pages/VersionDetailPage";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const {
  navigateMock,
  listProjectsApiMock,
  getVersionMock,
  readZadanieMock,
  writeZadanieMock,
  postPipelineActionApiMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  listProjectsApiMock: vi.fn(),
  getVersionMock: vi.fn(),
  readZadanieMock: vi.fn(),
  writeZadanieMock: vi.fn(),
  postPipelineActionApiMock: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock, useParams: () => ({ slug: "demo", versionId: "ver-1" }) };
});

vi.mock("@/services/api/projects", () => ({ listProjectsApi: listProjectsApiMock }));
vi.mock("@/services/api/versions", () => ({
  getVersion: getVersionMock,
  readZadanie: readZadanieMock,
  writeZadanie: writeZadanieMock,
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: postPipelineActionApiMock }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: vi.fn(), setSelectedVersion: vi.fn() }),
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const project = { id: "proj-1", name: "Demo", slug: "demo" } as ProjectRead;

const version = {
  id: "ver-1",
  project_id: "proj-1",
  version_number: "0.1.0",
  name: null,
  status: "planned",
  description: null,
  target_date: null,
  release_date: null,
  created_at: "2026-07-26T10:00:00Z",
  updated_at: "2026-07-26T10:00:00Z",
  epic_count: 0,
  epics_done: 0,
  bug_count: 0,
} as Version;

describe("VersionDetailPage — an unreadable Zadanie is its own state", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    listProjectsApiMock.mockReset().mockResolvedValue({ items: [project] });
    getVersionMock.mockReset().mockResolvedValue(version);
    readZadanieMock.mockReset();
    writeZadanieMock.mockReset().mockResolvedValue({ relative_path: "x.md", status: "ok" });
    postPipelineActionApiMock.mockReset().mockResolvedValue({});
  });

  it("locks the editor and both actions when the read fails, instead of showing an empty Zadanie", async () => {
    readZadanieMock.mockRejectedValue(new Error("Network down"));

    render(<VersionDetailPage />);

    await waitFor(() => expect(screen.getByText(/Zadanie sa nepodarilo načítať/)).toBeInTheDocument());
    // No editor at all — a disabled empty box would still read as "nothing written yet".
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Uložiť Zadanie/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Spustiť tvorbu špecifikácie/ })).toBeDisabled();
    // …and the intro that invites "leave it empty and start" is gone, not merely contradicted below it.
    expect(screen.queryByText(/ak ho necháš prázdne/)).not.toBeInTheDocument();
  });

  it("never writes over a Zadanie it could not read", async () => {
    readZadanieMock.mockRejectedValue(new Error("HTTP 403"));

    render(<VersionDetailPage />);
    await screen.findByText(/Zadanie sa nepodarilo načítať/);

    await userEvent.click(screen.getByRole("button", { name: /Uložiť Zadanie/ }));
    await userEvent.click(screen.getByRole("button", { name: /Spustiť tvorbu špecifikácie/ }));

    expect(writeZadanieMock).not.toHaveBeenCalled();
    expect(postPipelineActionApiMock).not.toHaveBeenCalled();
  });

  it("unlocks the editor once a retry reads the Zadanie", async () => {
    readZadanieMock
      .mockRejectedValueOnce(new Error("Network down"))
      .mockResolvedValueOnce({ content: "Skutočné zadanie na disku." });

    render(<VersionDetailPage />);
    await screen.findByText(/Zadanie sa nepodarilo načítať/);

    await userEvent.click(screen.getByRole("button", { name: /Skúsiť znova/ }));

    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Skutočné zadanie na disku."));
    expect(screen.queryByText(/Zadanie sa nepodarilo načítať/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Uložiť Zadanie/ })).toBeEnabled();
  });

  it("keeps a genuinely empty Zadanie editable and startable (the backend's own '' answer)", async () => {
    readZadanieMock.mockResolvedValue({ content: "" });

    render(<VersionDetailPage />);

    const textarea = await screen.findByRole("textbox");
    expect(textarea).toHaveValue("");
    expect(screen.queryByText(/Zadanie sa nepodarilo načítať/)).not.toBeInTheDocument();
    // Empty is a legitimate brief — Spustiť stays open, only Uložiť waits for text.
    expect(screen.getByRole("button", { name: /Spustiť tvorbu špecifikácie/ })).toBeEnabled();

    await userEvent.type(textarea, "Nové zadanie");
    await userEvent.click(screen.getByRole("button", { name: /Uložiť Zadanie/ }));
    await waitFor(() => expect(writeZadanieMock).toHaveBeenCalledWith("ver-1", "Nové zadanie"));
  });
});
