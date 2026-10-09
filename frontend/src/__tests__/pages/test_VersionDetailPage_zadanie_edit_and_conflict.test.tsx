/**
 * DEV-40 — a saved Zadanie can be edited; a real conflict shows what is on disk and offers a choice.
 *
 * 09.10.2026, Career Asistent: every save after the first ended in „Uloženie Zadania zlyhalo — Conflict". The
 * guard refused ANY text different from the disk and the page never said which text it had started from, nor
 * offered to replace. Director: „Áno, rozšír DEV-40 a oprav to celé".
 *
 * Pinned: an edit sends the text the editor loaded (`basedOn`) and saves; a conflict (the disk changed meanwhile)
 * shows the disk text and offers Nahradiť / Doplniť / Prevziať — nothing is written without his click.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import VersionDetailPage from "@/pages/VersionDetailPage";
import { ApiError } from "@/services/api";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

const { listProjectsApiMock, getVersionMock, readZadanieMock, writeZadanieMock } = vi.hoisted(() => ({
  listProjectsApiMock: vi.fn(),
  getVersionMock: vi.fn(),
  readZadanieMock: vi.fn(),
  writeZadanieMock: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn(), useParams: () => ({ slug: "demo", versionId: "ver-1" }) };
});
vi.mock("@/services/api/projects", () => ({ listProjectsApi: listProjectsApiMock }));
vi.mock("@/services/api/versions", () => ({
  getVersion: getVersionMock,
  readZadanie: readZadanieMock,
  writeZadanie: writeZadanieMock,
  getVersionSettings: vi.fn(() => Promise.resolve({ version_number_lock_reason: null })),
  updateVersion: vi.fn(),
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: vi.fn(), setSelectedVersion: vi.fn() }),
}));

const project = { id: "proj-1", name: "Career Asistent", slug: "demo" } as ProjectRead;
const version = {
  id: "ver-1",
  project_id: "proj-1",
  version_number: "0.1.0",
  name: null,
  status: "planned",
  description: null,
  target_date: null,
  release_date: null,
  created_at: "2026-10-09T10:00:00Z",
  updated_at: "2026-10-09T10:00:00Z",
  epic_count: 0,
  epics_done: 0,
  bug_count: 0,
} as Version;

const ON_DISK = "Zadanie v1";
const MINE = "Zadanie v1 A ešte veta.";
const AGENT = "Zadanie v2 — zapísal agent.";
const CLASH = new ApiError(409, "Conflict", {
  detail: { message: "Pre túto verziu už zadanie existuje (1 riadkov).", existing: AGENT, relative_path: "x.md" },
});

const editor = () => screen.findByPlaceholderText(/Opíš, čo má aplikácia robiť/i);
const ulozit = () => screen.getByRole("button", { name: /Uložiť Zadanie/ });
// Once saved, the same button says so and stays shut — nothing left to save.
const ulozene = () => screen.getByRole("button", { name: /Zadanie uložené/ });

async function editAndSave() {
  render(<VersionDetailPage />);
  await userEvent.type(await editor(), " A ešte veta.");
  await userEvent.click(ulozit());
}

beforeEach(() => {
    window.localStorage.clear(); // DEV-32: the Zadanie keeps a draft — one test must not leave it for the next
  listProjectsApiMock.mockReset().mockResolvedValue({ items: [project] });
  getVersionMock.mockReset().mockResolvedValue(version);
  readZadanieMock.mockReset().mockResolvedValue({ content: ON_DISK });
  writeZadanieMock.mockReset().mockResolvedValue({ relative_path: "x.md", status: "saved" });
});

describe("DEV-40 — editing a saved Zadanie", () => {
  it("an edit says which text it started from, and is saved", async () => {
    await editAndSave();

    await waitFor(() => expect(writeZadanieMock).toHaveBeenCalledWith("ver-1", MINE, { basedOn: ON_DISK }));
    expect(screen.queryByText(AGENT)).not.toBeInTheDocument();
    await waitFor(() => expect(ulozene()).toBeDisabled());
  });

  it("a real conflict shows the disk text and three choices, and writes nothing on its own", async () => {
    writeZadanieMock.mockRejectedValueOnce(CLASH);
    await editAndSave();

    expect(await screen.findByText(AGENT)).toBeInTheDocument();
    expect(screen.getByText(/Pre túto verziu už zadanie existuje/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nahradiť mojím textom" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Doplniť môj text na koniec" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prevziať text z disku do poľa" })).toBeInTheDocument();
    expect(writeZadanieMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Uloženie Zadania zlyhalo/)).not.toBeInTheDocument();
  });

  it("„Nahradiť“ writes his text over the disk, by his decision", async () => {
    writeZadanieMock.mockRejectedValueOnce(CLASH);
    await editAndSave();
    await userEvent.click(await screen.findByRole("button", { name: "Nahradiť mojím textom" }));

    await waitFor(() => expect(writeZadanieMock).toHaveBeenLastCalledWith("ver-1", MINE, { replaceExisting: true }));
    await waitFor(() => expect(screen.queryByText(AGENT)).not.toBeInTheDocument());
    expect(await editor()).toHaveValue(MINE);
    expect(ulozene()).toBeDisabled();
  });

  it("„Doplniť“ puts his text after the disk text, checked against the disk", async () => {
    writeZadanieMock.mockRejectedValueOnce(CLASH);
    await editAndSave();
    await userEvent.click(await screen.findByRole("button", { name: "Doplniť môj text na koniec" }));

    const combined = `${AGENT}\n\n${MINE}`;
    await waitFor(() => expect(writeZadanieMock).toHaveBeenLastCalledWith("ver-1", combined, { basedOn: AGENT }));
    expect(await editor()).toHaveValue(combined);
    expect(ulozene()).toBeDisabled();
  });

  it("„Prevziať“ loads the disk text into the editor and writes nothing", async () => {
    writeZadanieMock.mockRejectedValueOnce(CLASH);
    await editAndSave();
    await userEvent.click(await screen.findByRole("button", { name: "Prevziať text z disku do poľa" }));

    expect(await editor()).toHaveValue(AGENT);
    expect(writeZadanieMock).toHaveBeenCalledTimes(1);
    expect(ulozene()).toBeDisabled(); // the editor now holds exactly what is on disk
    expect(screen.queryByRole("button", { name: "Nahradiť mojím textom" })).not.toBeInTheDocument();
  });
});

describe("DEV-32 — an unsaved Zadanie survives a trip to another screen", () => {
  it("leaving and coming back restores his text and says it is his earlier draft; saving forgets it", async () => {
    const first = render(<VersionDetailPage />);
    await userEvent.type(await editor(), " A ešte veta.");
    first.unmount(); // he clicked over to another screen

    render(<VersionDetailPage />);
    expect(await editor()).toHaveValue(MINE);
    expect(screen.getByText("Obnovený rozpísaný text — pokračuj, alebo ho prepíš.")).toBeInTheDocument();

    await userEvent.click(ulozit());
    await waitFor(() => expect(writeZadanieMock).toHaveBeenCalledWith("ver-1", MINE, { basedOn: ON_DISK }));
    await waitFor(() => expect(window.localStorage.getItem("nex.draft.zadanie-verzie.ver-1")).toBeNull());
  });

  it("a text typed back to what is on disk is no draft — nothing is kept and nothing is announced", async () => {
    const first = render(<VersionDetailPage />);
    await userEvent.type(await editor(), " X{Backspace}{Backspace}"); // edited, then back to the disk text
    expect(await editor()).toHaveValue(ON_DISK);
    first.unmount();

    render(<VersionDetailPage />);
    expect(await editor()).toHaveValue(ON_DISK);
    expect(screen.queryByText(/Obnovený rozpísaný text/)).not.toBeInTheDocument();
    expect(window.localStorage.getItem("nex.draft.zadanie-verzie.ver-1")).toBeNull();
  });
});
