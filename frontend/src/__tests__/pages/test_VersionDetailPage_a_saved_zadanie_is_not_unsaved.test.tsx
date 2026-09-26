/**
 * VersionDetailPage — Zadanie načítané z disku NIE JE neuložené (ICCINT-152).
 *
 * **Čo tomu predchádzalo.** 26.09.2026 Director prešiel novú cestu naživo: Dedo položil zadanie na stránku
 * projektu, on ho odoslal, verzia vznikla a text sa načítal späť zo súboru, z ktorého číta Príprava. A na
 * konci sa nedalo pokračovať — „Spustiť tvorbu špecifikácie" bolo zašednuté.
 *
 * Príčina nebola v tej novej ceste. Stránka nesledovala, či sa text v editore LÍŠI od toho na disku;
 * sledovala, či niekto v TOMTO okne stlačil „Uložiť Zadanie" (`zadanieSaved`, počiatočná hodnota `false`).
 * Zadanie, ktoré prišlo z disku nedotknuté, bolo pre ňu neuložené — a narazil na to každý, kto sa vrátil na
 * verziu s už uloženým zadaním, nielen cesta od Deda.
 *
 * ⚠️ Čo tieto stráže NEDOVOLIA zmeniť:
 *
 * 1. **Nedotknutý text z disku spustenie neblokuje.** To je jadro chyby.
 * 2. **Upravený text ho blokuje.** Keby oprava tlačidlo len natrvalo otvorila, Príprava by si prečítala inú
 *    vec, než má Manažér na obrazovke — a to je horšia chyba než tá pôvodná.
 * 3. **Rozhoduje porovnanie s diskom, nie stlačenie tlačidla.** Preto sa spustenie otvorí aj vtedy, keď sa
 *    text vráti na pôvodné znenie, a aj po úspešnom opätovnom načítaní.
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
  getVersionSettings: vi.fn(() => Promise.resolve({ version_number_lock_reason: null })),
  updateVersion: vi.fn(),
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
  version_number: "1.6.0",
  name: null,
  status: "planned",
  description: null,
  target_date: null,
  release_date: null,
  created_at: "2026-09-26T10:00:00Z",
  updated_at: "2026-09-26T10:00:00Z",
  epic_count: 0,
  epics_done: 0,
  bug_count: 0,
} as Version;

/** Presne to znenie, ktoré Dedo položil a cesta zapísala na disk. */
const NA_DISKU = "Povýš zdieľaný kit nex-shared z v0.8.1 na v0.19.0.";

const spustit = () => screen.getByRole("button", { name: /Spustiť tvorbu špecifikácie/ });
const ulozit = () => screen.getByRole("button", { name: /Uložiť Zadanie|Zadanie uložené/ });

describe("VersionDetailPage — uložené Zadanie nie je neuložené", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    listProjectsApiMock.mockReset().mockResolvedValue({ items: [project] });
    getVersionMock.mockReset().mockResolvedValue(version);
    readZadanieMock.mockReset().mockResolvedValue({ content: NA_DISKU });
    writeZadanieMock.mockReset().mockResolvedValue({ relative_path: "x.md", status: "ok" });
    postPipelineActionApiMock.mockReset().mockResolvedValue({});
  });

  it("⚠️ jadro tiketu: Zadanie načítané z disku sa dá hneď spustiť, bez opakovaného ukladania", async () => {
    render(<VersionDetailPage />);

    await waitFor(() => expect(screen.getByPlaceholderText(/Opíš, čo má aplikácia robiť/i)).toHaveValue(NA_DISKU));
    expect(spustit()).toBeEnabled();

    await userEvent.click(spustit());
    await waitFor(() => expect(postPipelineActionApiMock).toHaveBeenCalledWith("ver-1", { action: "start" }));
  });

  it("povie, že to, čo je na obrazovke, je aj uložené — nepýta si prácu, ktorá je hotová", async () => {
    render(<VersionDetailPage />);

    await waitFor(() => expect(screen.getByText(/Zadanie uložené/)).toBeInTheDocument());
    expect(ulozit()).toBeDisabled();
  });

  it("⚠️ opačný smer: upravený text spustenie zavrie, kým sa neuloží", async () => {
    render(<VersionDetailPage />);

    const editor = await screen.findByPlaceholderText(/Opíš, čo má aplikácia robiť/i);
    await userEvent.type(editor, " A ešte jedna vec.");

    expect(spustit()).toBeDisabled();
    expect(ulozit()).toBeEnabled();

    await userEvent.click(ulozit());

    await waitFor(() => expect(writeZadanieMock).toHaveBeenCalledWith("ver-1", `${NA_DISKU} A ešte jedna vec.`));
    await waitFor(() => expect(spustit()).toBeEnabled());
  });

  it("⚠️ opačný smer: vymazaný text spustenie tiež zavrie — inak by Príprava čítala zo súboru niečo iné", async () => {
    render(<VersionDetailPage />);

    const editor = await screen.findByPlaceholderText(/Opíš, čo má aplikácia robiť/i);
    await userEvent.clear(editor);

    expect(spustit()).toBeDisabled();
  });

  it("rozhoduje porovnanie s diskom: návrat k pôvodnému zneniu spustenie zase otvorí", async () => {
    render(<VersionDetailPage />);

    const editor = await screen.findByPlaceholderText(/Opíš, čo má aplikácia robiť/i);
    await userEvent.type(editor, "!");
    expect(spustit()).toBeDisabled();

    await userEvent.type(editor, "{backspace}");

    await waitFor(() => expect(spustit()).toBeEnabled());
    expect(writeZadanieMock).not.toHaveBeenCalled();
  });

  it("aj po opätovnom načítaní platí to isté — nie je to pamäť na stlačené tlačidlo", async () => {
    readZadanieMock
      .mockReset()
      .mockRejectedValueOnce(new Error("Network down"))
      .mockResolvedValueOnce({ content: NA_DISKU });

    render(<VersionDetailPage />);
    await screen.findByText(/Zadanie sa nepodarilo načítať/);

    await userEvent.click(screen.getByRole("button", { name: /Skúsiť znova/ }));

    await waitFor(() => expect(screen.getByPlaceholderText(/Opíš, čo má aplikácia robiť/i)).toHaveValue(NA_DISKU));
    expect(spustit()).toBeEnabled();
  });
});
