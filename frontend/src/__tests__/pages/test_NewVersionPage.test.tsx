/**
 * NewVersionPage — a failure on the founding screen must be SEEN.
 *
 * The dirty-tree guard ("Uložiť ich" / "Zahodiť") and the nex-shared upgrade all ran through empty
 * catch blocks: a rejected commit flipped the button back and left the same warning card standing,
 * with nothing said, and an upgrade whose pin could not be committed dismissed the prompt and
 * reported the library up to date while the tree it had just dirtied went unnoticed. These tests
 * pin the honest behaviour — every failure renders, and the guard re-checks the tree for real.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import { ApiError } from "@/services/api";
import type { GitStatus, NexsharedStatus } from "@/services/api/projects";
import type { ProjectRead } from "@/types";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const {
  navigateMock,
  listProjectsApiMock,
  getNexsharedStatusApiMock,
  upgradeNexsharedApiMock,
  getGitStatusApiMock,
  commitGitApiMock,
  discardGitApiMock,
  listVersionsMock,
  createVersionMock,
  writeZadanieMock,
  updateVersionMock,
  peekZadanieOnDiskMock,
  postPipelineActionApiMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  listProjectsApiMock: vi.fn(),
  getNexsharedStatusApiMock: vi.fn(),
  upgradeNexsharedApiMock: vi.fn(),
  getGitStatusApiMock: vi.fn(),
  commitGitApiMock: vi.fn(),
  discardGitApiMock: vi.fn(),
  listVersionsMock: vi.fn(),
  createVersionMock: vi.fn(),
  writeZadanieMock: vi.fn(),
  updateVersionMock: vi.fn(),
  peekZadanieOnDiskMock: vi.fn(),
  postPipelineActionApiMock: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock, useParams: () => ({ slug: "demo" }) };
});

vi.mock("@/services/api/projects", () => ({
  listProjectsApi: listProjectsApiMock,
  getNexsharedStatusApi: getNexsharedStatusApiMock,
  upgradeNexsharedApi: upgradeNexsharedApiMock,
  getGitStatusApi: getGitStatusApiMock,
  commitGitApi: commitGitApiMock,
  discardGitApi: discardGitApiMock,
}));
vi.mock("@/services/api/versions", () => ({
  listVersions: listVersionsMock,
  createVersion: createVersionMock,
  writeZadanie: writeZadanieMock,
  updateVersion: updateVersionMock,
  peekZadanieOnDisk: peekZadanieOnDiskMock,
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: postPipelineActionApiMock }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: vi.fn(), setSelectedVersion: vi.fn() }),
}));

// ── Fixtures ───────────────────────────────────────────────────────────────────

const project: ProjectRead = {
  id: "p1",
  name: "Demo",
  slug: "demo",
  type: "standard",
  auth_mode: "password",
  description: "",
  status: "active",
  backend_port: null,
  frontend_port: null,
  db_port: null,
  repo_url: null,
  source_path: "/opt/projects/demo",
  kb_path: null,
  guardian_enabled: false,
  setup_warnings: [],
  custom_development_enabled: false,
  created_by: "u1",
  owner_id: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  has_prod_deploy: false,
};

const dirtyTree: GitStatus = {
  clean: false,
  dirty_count: 2,
  files: [
    { code: " M", path: "backend/api/routes/projects.py" },
    { code: "??", path: "docs/notes.md" },
  ],
  truncated: false,
};
const cleanTree: GitStatus = { clean: true, dirty_count: 0, files: [], truncated: false };

/** Verzia tak, ako ju engine naozaj vracia — mock, ktorý je chudobnejší, meria sám seba. */
const version = {
  id: "v1",
  project_id: "p1",
  version_number: "0.1.0", // to isté číslo, aké formulár pri prázdnom zozname navrhne
  name: null as string | null,
  status: "planned" as const,
  description: null,
  target_date: null,
  release_date: null,
  created_at: "2026-09-09T00:00:00Z",
  updated_at: "2026-09-09T00:00:00Z",
};

const behindNexshared: NexsharedStatus = {
  current: "0.18.0",
  latest: "0.19.0",
  behind: 1,
  up_to_date: false,
  changelog: [{ version: "0.19.0", body: "Nové tokeny." }],
};

beforeEach(() => {
  vi.clearAllMocks();
  listProjectsApiMock.mockResolvedValue({ items: [project], total: 1, skip: 0, limit: 100 });
  listVersionsMock.mockResolvedValue([]);
  getNexsharedStatusApiMock.mockResolvedValue({ ...behindNexshared, behind: 0, up_to_date: true, changelog: [] });
  getGitStatusApiMock.mockResolvedValue(cleanTree);
  commitGitApiMock.mockResolvedValue({ ok: true });
  discardGitApiMock.mockResolvedValue({ ok: true });
  // Nahliadnutie na disk je súčasťou zakladania — predvolene tam nič nie je (bežný prípad).
  peekZadanieOnDiskMock.mockResolvedValue({ content: "", relative_path: "" });
});

async function renderPage() {
  const NewVersionPage = (await import("@/pages/NewVersionPage")).default;
  render(<NewVersionPage />);
}

describe("NewVersionPage — dirty-tree guard reports its failures", () => {
  it('says why "Uložiť ich" failed instead of silently re-showing the guard', async () => {
    getGitStatusApiMock.mockResolvedValue(dirtyTree);
    // What the container actually answers when the project's own pre-commit hook cannot run there.
    commitGitApiMock.mockRejectedValue(new ApiError(400, "Commit zlyhal: pre-commit hook (ruff) not found"));

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /uložiť ich/i }));

    expect(await screen.findByText(/Uloženie zmien zlyhalo/i)).toBeInTheDocument();
    // The raw backend reason stays available behind the "Technický detail" disclosure.
    expect(screen.getByText(/pre-commit hook \(ruff\) not found/i)).toBeInTheDocument();
    // The guard stays up (the tree is still dirty) and founding stays blocked.
    expect(screen.getByRole("button", { name: /uložiť ich/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeDisabled();
  });

  it('says why "Zahodiť" failed', async () => {
    getGitStatusApiMock.mockResolvedValue(dirtyTree);
    discardGitApiMock.mockRejectedValue(new ApiError(400, "Zahodenie zmien zlyhalo"));

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /^zahodiť$/i }));
    await userEvent.click(screen.getByRole("button", { name: /naozaj zahodiť/i }));

    expect(await screen.findByText(/Zahodenie zmien zlyhalo — /i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeDisabled();
  });

  it("clears the guard when the commit really succeeds", async () => {
    getGitStatusApiMock.mockResolvedValueOnce(dirtyTree).mockResolvedValue(cleanTree);

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /uložiť ich/i }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /uložiť ich/i })).not.toBeInTheDocument());
    expect(screen.queryByText(/zlyhalo/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeEnabled();
  });

  it("does not pretend the tree is clean when the preflight itself fails", async () => {
    getGitStatusApiMock.mockRejectedValue(new ApiError(500, "git unavailable"));

    await renderPage();

    expect(await screen.findByText(/Overenie neuložených zmien projektu zlyhalo/i)).toBeInTheDocument();
  });
});

describe("NewVersionPage — nex-shared upgrade tells the truth about committed", () => {
  beforeEach(() => {
    getNexsharedStatusApiMock.mockResolvedValue(behindNexshared);
  });

  it("reports a written-but-unsaved pin and re-checks the tree instead of claiming up to date", async () => {
    upgradeNexsharedApiMock.mockResolvedValue({ upgraded: true, target_version: "0.19.0", committed: false });
    // The upgrade dirties the tree: the pin was rewritten but never committed.
    getGitStatusApiMock.mockResolvedValueOnce(cleanTree).mockResolvedValue(dirtyTree);

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));

    expect(await screen.findByText(/nepodarilo sa ho uložiť/i)).toBeInTheDocument();
    // The guard re-evaluated against reality: the now-dirty tree blocks founding again.
    await waitFor(() => expect(getGitStatusApiMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: /uložiť ich/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeDisabled();
  });

  it("drops the unsaved-pin note once the tree is committed (the commit saved the pin)", async () => {
    upgradeNexsharedApiMock.mockResolvedValue({ upgraded: true, target_version: "0.19.0", committed: false });
    getGitStatusApiMock.mockResolvedValueOnce(cleanTree).mockResolvedValueOnce(dirtyTree).mockResolvedValue(cleanTree);

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));
    expect(await screen.findByText(/nepodarilo sa ho uložiť/i)).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: /uložiť ich/i }));

    await waitFor(() => expect(screen.queryByText(/nepodarilo sa ho uložiť/i)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeEnabled();
  });

  it("dismisses the prompt only when the pin was really committed", async () => {
    upgradeNexsharedApiMock.mockResolvedValue({ upgraded: true, target_version: "0.19.0", committed: true });

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));

    await waitFor(() => expect(screen.queryByText(/K dispozícii je novší nex-shared/i)).not.toBeInTheDocument());
    expect(screen.queryByText(/nepodarilo sa ho uložiť/i)).not.toBeInTheDocument();
    // Even the happy path re-reads the tree — the commit could have left something behind.
    await waitFor(() => expect(getGitStatusApiMock).toHaveBeenCalledTimes(2));
  });

  it("renders a rejected upgrade instead of swallowing it", async () => {
    upgradeNexsharedApiMock.mockRejectedValue(new ApiError(400, "chýba nex-shared pin"));

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));

    expect(await screen.findByText(/Povýšenie nex-shared na v0\.19\.0 zlyhalo/i)).toBeInTheDocument();
    // Still offered — the Manažér can retry or stay.
    expect(screen.getByRole("button", { name: /povýšiť na v0\.19\.0/i })).toBeEnabled();
  });
});

describe("NewVersionPage — existujúce zadanie sa neprepíše ticho (ICCINT-71)", () => {
  it("ukáže, čo v súbore je, namiesto všeobecnej hlášky o zlyhaní", async () => {
    // Mock musí vrátiť to, čo engine naozaj vracia — inak formulár nemá s čím porovnať
    // prepísanú hlavičku (ICCINT-91) a test meria tvar mocku, nie správanie stránky.
    // ⚠️ Mock musí vrátiť to, čo do zakladania naozaj išlo. Keby vrátil hlavičku, akú formulár nikdy
    // neposlal, druhý pokus by ju „opravoval" a test by meral tvar mocku, nie správanie stránky.
    createVersionMock.mockResolvedValue({ ...version, id: "v-1", description: "Jedna veta." });
    writeZadanieMock.mockRejectedValue(
      new ApiError(409, "conflict", {
        detail: {
          message: "Pre túto verziu už zadanie existuje (71 riadkov).",
          existing: "# Zákaznícka špecifikácia\n\nSedemdesiat riadkov práce.",
        },
      }),
    );

    await renderPage();
    await userEvent.type(await screen.findByPlaceholderText(/Opíš, čo má verzia priniesť/i), "Jedna veta.");
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));

    // Obsah, nie veta o obsahu. Manažér stratil 71 riadkov práve preto, že videl prázdne pole.
    expect(await screen.findByText(/Sedemdesiat riadkov práce/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prevziať toto zadanie do poľa/i })).toBeInTheDocument();
  });

  it("druhý pokus verziu nezakladá znova — inak sa formulár zasekne", async () => {
    // Mock musí vrátiť to, čo engine naozaj vracia — inak formulár nemá s čím porovnať
    // prepísanú hlavičku (ICCINT-91) a test meria tvar mocku, nie správanie stránky.
    // ⚠️ Mock musí vrátiť to, čo do zakladania naozaj išlo. Keby vrátil hlavičku, akú formulár nikdy
    // neposlal, druhý pokus by ju „opravoval" a test by meral tvar mocku, nie správanie stránky.
    createVersionMock.mockResolvedValue({ ...version, id: "v-1", description: "Jedna veta." });
    writeZadanieMock.mockRejectedValue(
      new ApiError(409, "conflict", {
        detail: { message: "Už existuje.", existing: "# Obsah\n" },
      }),
    );

    await renderPage();
    await userEvent.type(await screen.findByPlaceholderText(/Opíš, čo má verzia priniesť/i), "Jedna veta.");
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));
    await screen.findByRole("button", { name: /prevziať toto zadanie do poľa/i });

    // Verzia vzniká PRED zápisom Zadania. Bez pamäte by druhý pokus padol na „verzia už existuje"
    // a Manažér by sa z formulára nedostal — zmerané na 0.2.0 dňa 07.09.2026.
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));

    await waitFor(() => expect(writeZadanieMock).toHaveBeenCalledTimes(2));
    expect(createVersionMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * ICCINT-90 / ICCINT-91 — zakladanie verzie nesmie nič zatajiť ani ticho zahodiť.
 *
 * Oba nálezy pochádzajú z jedného sedenia (NEX Manager 1.1.0, 09.09.2026) a oba majú tú istú povahu:
 * obrazovka sa tvárila, že je všetko v poriadku, hoci nebolo. Raz mlčala o zadaní, ktoré už na disku
 * ležalo; druhýkrát prijala text do polí, ktoré sa už nikam neposielali.
 */
describe("NewVersionPage — pripravené zadanie a úpravy hlavičky", () => {
  it("ukáže pripravené zadanie UŽ pri zakladaní, nie až po zrážke (ICCINT-90)", async () => {
    peekZadanieOnDiskMock.mockResolvedValue({
      content: "Sedem strán rozmyslenej práce.",
      relative_path: "docs/specs/versions/v0.1.0/customer-requirements.md",
    });

    await renderPage();

    expect(await screen.findByText(/už na disku pripravené zadanie leží/i)).toBeInTheDocument();
    expect(screen.getByText("Sedem strán rozmyslenej práce.")).toBeInTheDocument();
    // ⚠️ Iba ukazuje. Keby si do poľa siahlo samo, prepísalo by rozpísaný text Manažéra.
    expect(screen.getByPlaceholderText(/Opíš, čo má verzia priniesť/i)).toHaveValue("");
    // A nič sa nezakladalo — nahliadnutie je otázka, nie zásah.
    expect(createVersionMock).not.toHaveBeenCalled();
  });

  it("nezakričí o zadaní, keď na disku pre toto číslo verzie nič nie je", async () => {
    await renderPage();
    await waitFor(() => expect(peekZadanieOnDiskMock).toHaveBeenCalled());

    expect(screen.queryByText(/pripravené zadanie leží/i)).not.toBeInTheDocument();
  });

  it("po prvom neúspechu uloží prepísaný názov, nie ten pôvodný (ICCINT-91)", async () => {
    createVersionMock.mockResolvedValue({ ...version, name: "Inštalovateľná appka", description: "Moje zadanie." });
    // Prvé uloženie odmietne poistka proti prepísaniu — verzia však už vznikla.
    writeZadanieMock.mockRejectedValueOnce(
      new ApiError(409, "conflict", {
        detail: { existing: "Text na disku.", message: "Pre túto verziu už zadanie existuje." },
      }),
    );
    updateVersionMock.mockImplementation((_id: string, data: Record<string, unknown>) =>
      Promise.resolve({ ...version, ...data }),
    );
    writeZadanieMock.mockResolvedValue({ relative_path: "x", status: "saved" });

    await renderPage();
    await userEvent.type(await screen.findByPlaceholderText(/napr. platobný modul/i), "Inštalovateľná appka");
    await userEvent.type(screen.getByPlaceholderText(/Opíš, čo má verzia priniesť/i), "Moje zadanie.");
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));
    expect(await screen.findByRole("button", { name: /prevziať toto zadanie do poľa/i })).toBeInTheDocument();

    // Director prepíše názov a uloží znova — presne to, čo sa 09.09.2026 ticho stratilo.
    const nazov = screen.getByPlaceholderText(/napr. platobný modul/i);
    await userEvent.clear(nazov);
    await userEvent.type(nazov, "Inštalovateľná aplikácia PWA");
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));

    await waitFor(() => expect(updateVersionMock).toHaveBeenCalled());
    expect(updateVersionMock).toHaveBeenCalledWith("v1", expect.objectContaining({ name: "Inštalovateľná aplikácia PWA" }));
    // A verzia sa nezakladá druhýkrát — na to je pamäť z ICCINT-71.
    expect(createVersionMock).toHaveBeenCalledTimes(1);
  });

  it("nezaťažuje engine úpravou, keď sa v hlavičke nič nezmenilo", async () => {
    // Verzia sa vracia presne s tým, čo do nej pri zakladaní išlo — takže druhý pokus nemá čo meniť.
    createVersionMock.mockResolvedValue({ ...version, description: "Moje zadanie." });
    writeZadanieMock.mockRejectedValueOnce(
      new ApiError(409, "conflict", {
        detail: { existing: "Text na disku.", message: "Pre túto verziu už zadanie existuje." },
      }),
    );
    writeZadanieMock.mockResolvedValue({ relative_path: "x", status: "saved" });

    await renderPage();
    await userEvent.type(await screen.findByPlaceholderText(/Opíš, čo má verzia priniesť/i), "Moje zadanie.");
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));
    expect(await screen.findByRole("button", { name: /prevziať toto zadanie do poľa/i })).toBeInTheDocument();

    // Druhý pokus bez jediného doteku do hlavičky.
    await userEvent.click(screen.getByRole("button", { name: /uložiť zadanie/i }));

    await waitFor(() => expect(writeZadanieMock).toHaveBeenCalledTimes(2));
    expect(updateVersionMock).not.toHaveBeenCalled();
  });
});
