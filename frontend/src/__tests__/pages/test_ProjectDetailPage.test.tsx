/**
 * ProjectDetailPage — Fast-Fix Lane entry (F-009 §4 CR-B, CR-NS-095).
 *
 * The "Rýchla oprava" button opens a modal; submitting the directive POSTs to
 * /pipeline/fast-fix, pins the auto-created PATCH version into the active context
 * (project FIRST, then version), and navigates to the Vývoj board (/vyvoj,
 * renamed from /cockpit in CR-V2-019).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const {
  navigateMock,
  listProjectsApiMock,
  getProjectApiMock,
  deleteProjectApiMock,
  reassignProjectApiMock,
  projectAssignmentsApiMock,
  listUsersApiMock,
  listVersionsMock,
  getVersionMock,
  startFastFixApiMock,
  setSelectedProjectMock,
  setSelectedVersionMock,
  authStateMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  listProjectsApiMock: vi.fn(),
  getProjectApiMock: vi.fn(),
  deleteProjectApiMock: vi.fn(),
  reassignProjectApiMock: vi.fn(),
  projectAssignmentsApiMock: vi.fn(),
  listUsersApiMock: vi.fn(),
  listVersionsMock: vi.fn(),
  getVersionMock: vi.fn(),
  startFastFixApiMock: vi.fn(),
  setSelectedProjectMock: vi.fn(),
  setSelectedVersionMock: vi.fn(),
  authStateMock: { user: { role: "ri", username: "admin", id: "u-admin" } as { role: string; username: string; id: string } | null },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useParams: () => ({ slug: "demo" }),
    useLocation: () => ({ pathname: "/projects/demo", search: "", hash: "", key: "t", state: null }),
  };
});

vi.mock("@/services/api/projects", () => ({
  listProjectsApi: listProjectsApiMock,
  getProjectApi: getProjectApiMock,
  deleteProjectApi: deleteProjectApiMock,
  // ICCINT-78: stránka si pýta históriu presunov pri každom otvorení. Bez tejto atrapy sa efekt zosype
  // a spadne celé vykreslenie — nie kvôli tomu, čo test meria.
  reassignProjectApi: reassignProjectApiMock,
  projectAssignmentsApi: projectAssignmentsApiMock,
}));
vi.mock("@/services/api/users", () => ({ listUsersApi: listUsersApiMock }));
vi.mock("@/services/api/versions", () => ({ listVersions: listVersionsMock, getVersion: getVersionMock }));
vi.mock("@/services/api/pipeline", () => ({ startFastFixApi: startFastFixApiMock }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: setSelectedProjectMock, setSelectedVersion: setSelectedVersionMock }),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) => selector(authStateMock),
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
  source_path: null,
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

const baseVersion: Version = {
  id: "v1",
  project_id: "p1",
  version_number: "v0.6.0",
  name: "Initial",
  status: "released",
  description: null,
  target_date: null,
  release_date: "2026-06-01",
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  epic_count: 0,
  epics_done: 0,
  bug_count: 0,
};

const patchVersion: Version = { ...baseVersion, id: "v2", version_number: "v0.6.1", name: "Rýchla oprava", status: "planned" };

beforeEach(() => {
  projectAssignmentsApiMock.mockResolvedValue([]);
  listUsersApiMock.mockResolvedValue({ items: [], total: 0 });
  vi.clearAllMocks();
  listProjectsApiMock.mockResolvedValue({ items: [project], total: 1, skip: 0, limit: 100 });
  getProjectApiMock.mockResolvedValue(project);
  deleteProjectApiMock.mockResolvedValue(undefined);
  listVersionsMock.mockResolvedValue([baseVersion]);
  getVersionMock.mockResolvedValue(patchVersion);
  startFastFixApiMock.mockResolvedValue({ version_id: "v2", board: { state: null, recent_messages: [] } });
  // The ACCOUNT named admin — the ri ROLE no longer confers anything over a project (permissions.ts).
  authStateMock.user = { role: "ri", username: "admin", id: "u-admin" };
});

async function importPage() {
  return (await import("@/pages/ProjectDetailPage")).default;
}

describe("ProjectDetailPage — Fast-Fix Lane (CR-NS-095)", () => {
  it("posts the directive, pins the patch version (project first), and opens the cockpit", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    // Entry button appears once the project + its (>0) versions load.
    const entry = await screen.findByRole("button", { name: /^rýchla oprava$/i });
    await userEvent.click(entry);

    await userEvent.type(screen.getByLabelText(/popis opravy/i), "Oprav preklep v sidebare.");
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(startFastFixApiMock).toHaveBeenCalledWith("p1", "Oprav preklep v sidebare."),
    );
    // CR-V2-019 (OQ-7): the build board route is /vyvoj (renamed from /cockpit).
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/vyvoj"));

    expect(getVersionMock).toHaveBeenCalledWith("v2");
    expect(setSelectedProjectMock).toHaveBeenCalledWith({ slug: "demo", name: "Demo" });
    expect(setSelectedVersionMock).toHaveBeenCalledWith({ versionId: "v2", versionNumber: "v0.6.1" });

    // Context must be pinned project-FIRST (setSelectedProject clears the version slot).
    const projOrder = setSelectedProjectMock.mock.invocationCallOrder[0] ?? 0;
    const verOrder = setSelectedVersionMock.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER;
    expect(projOrder).toBeLessThan(verOrder);
  });

  it("submit is disabled on an empty directive and the modal can be cancelled", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^rýchla oprava$/i }));
    expect(screen.getByRole("button", { name: /spustiť rýchlu opravu/i })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /zrušiť/i }));
    expect(screen.queryByLabelText(/popis opravy/i)).not.toBeInTheDocument();
    expect(startFastFixApiMock).not.toHaveBeenCalled();
  });

  it("surfaces a backend error without navigating away", async () => {
    startFastFixApiMock.mockRejectedValue(new Error("Project has no semver version to patch from"));
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^rýchla oprava$/i }));
    await userEvent.type(screen.getByLabelText(/popis opravy/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    expect(await screen.findByText(/no semver version to patch/i)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});

describe("ProjectDetailPage — guarded delete (CR-V2-027)", () => {
  it("requires typing DELETE, then deletes (GitHub by default) and returns to the list", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^zmazať projekt$/i }));

    const confirm = screen.getByRole("button", { name: /zmazať natrvalo/i });
    expect(confirm).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/na potvrdenie napíš/i), "ZMAZAŤ");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    await waitFor(() => expect(deleteProjectApiMock).toHaveBeenCalledWith("p1", true));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects"));
  });

  it("does not delete on a wrong confirmation word", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^zmazať projekt$/i }));
    await userEvent.type(screen.getByLabelText(/na potvrdenie napíš/i), "zmazať"); // wrong case

    expect(screen.getByRole("button", { name: /zmazať natrvalo/i })).toBeDisabled();
    expect(deleteProjectApiMock).not.toHaveBeenCalled();
  });

  it("disables delete for someone who is neither the owner nor admin", async () => {
    // A different user entirely: not `created_by` ("u1"), not the admin account. Under the old model a
    // role could rescue him; it cannot now.
    authStateMock.user = { role: "ri", username: "somebody", id: "u-other" };
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /^zmazať projekt$/i })).toBeDisabled();
  });

  it("disables delete once the project has a PROD deploy", async () => {
    getProjectApiMock.mockResolvedValue({ ...project, has_prod_deploy: true });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /^zmazať projekt$/i })).toBeDisabled();
  });
});

describe("ProjectDetailPage — the delete dialog names everything it destroys (audit 2026-07-28)", () => {
  /**
   * The delete path runs `docker compose down -v` on the project's UAT — the `-v` takes the volumes,
   * i.e. the customer's UAT DATABASE — and cascades away the project's customers with their whole
   * deploy/acceptance history. The dialog used to promise only "kontajnery + port". Nobody can consent
   * to losing data they were never told about, so these assertions are the consent itself.
   */
  async function openDeleteDialog() {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^zmazať projekt$/i }));
    return screen.getByRole("dialog");
  }

  it("says the UAT database and the customer's data in it go too", async () => {
    const dialog = await openDeleteDialog();

    expect(dialog).toHaveTextContent(/databáz/i); // the DB itself, not just containers
    expect(dialog).toHaveTextContent(/údaje, ktoré do nej zákazník v UAT zadal/i);
    // The old wording claimed the UAT teardown was containers + port and nothing more.
    expect(dialog).not.toHaveTextContent(/UAT prostredie \(kontajnery \+ port\)/i);
  });

  it("says the project's customers and its deploy/acceptance history go too", async () => {
    const dialog = await openDeleteDialog();

    expect(dialog).toHaveTextContent(/zákazníkov/i);
    expect(dialog).toHaveTextContent(/históriu nasadení a akceptácií/i);
  });

  it("still lists what it always listed — the warning grew, nothing was traded away", async () => {
    const dialog = await openDeleteDialog();

    expect(dialog).toHaveTextContent(/verzie, špecifikácie, návrhy, epiky, úlohy a chyby/i);
    expect(dialog).toHaveTextContent(/priečinok v znalostnej báze/i);
    expect(dialog).toHaveTextContent(/pracovný adresár na disku/i);
    expect(dialog).toHaveTextContent(/nevratne/i);
  });

  it("surfaces the backend's refusal when the UAT holds data, and stays on the page", async () => {
    // The guard the dialog is paired with: a UAT with a database can only be archived (409).
    deleteProjectApiMock.mockRejectedValue(
      new Error(
        "Projekt 'demo' má UAT prostredie 'demo' s databázou — jeho zmazanie by ju aj so všetkými " +
          "údajmi, ktoré do nej zákazník zadal, nenávratne odstránilo. Namiesto mazania ho archivuj.",
      ),
    );
    await openDeleteDialog();

    await userEvent.type(screen.getByLabelText(/na potvrdenie napíš/i), "ZMAZAŤ");
    await userEvent.click(screen.getByRole("button", { name: /zmazať natrvalo/i }));

    expect(await screen.findByText(/namiesto mazania ho archivuj/i)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});

// ─── Zverenie projektu (ICCINT-78 / 80 / 81) ──────────────────────────────────
//
// ⚠️ Panel zverenia bol do 08.09.2026 na obrazovke bez jedinej stráže — atrapy nižšie existovali len
// preto, aby sa stránka vôbec vykreslila. To je presne ten stav, v ktorom sa zmena „vyzerá hotovo“
// a nikto nevie, že prestala fungovať.

describe("ProjectDetailPage — zverenie projektu", () => {
  const tibor = {
    id: "u-tibi",
    username: "tibi",
    first_name: "Tibor",
    last_name: "Rausch",
    email: "t@example.test",
    role: "shu",
    is_active: true,
  };

  beforeEach(() => {
    listUsersApiMock.mockResolvedValue({ items: [tibor], total: 1 });
    getProjectApiMock.mockResolvedValue({ ...project, created_by: "u-tibi" });
    listProjectsApiMock.mockResolvedValue({
      items: [{ ...project, created_by: "u-tibi" }],
      total: 1,
      skip: 0,
      limit: 100,
    });
  });

  it("ponúka ľudí menom a priezviskom, nie prihlasovacím menom", async () => {
    // Director 08.09.2026: manažér vyberá človeka, nie účet. `tibi` si nemá v hlave prekladať na Tibora.
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    const volba = await screen.findByRole("option", { name: "Tibor Rausch" });
    expect(volba).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "tibi" })).not.toBeInTheDocument();
  });

  it("povie, komu projekt patrí teraz", async () => {
    // Panel dovtedy ponúkal, KOMU projekt zveriť, ale nepovedal, komu už patrí — a to je prvá otázka,
    // ktorú si pri ňom človek položí.
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    // Čaká sa na dopočítaný riadok, nie na jeho prvé vykreslenie: kým sa ľudia nenačítajú, stojí tam
    // trojbodka a stráž by prešla na polovičnom stave.
    await waitFor(() => expect(screen.getByText(/projekt patrí:/i)).toHaveTextContent("Tibor Rausch"));
  });

  it("keď upozornenia nemajú kam ísť, povie to nahlas a zo stránky neodíde", async () => {
    // ⚠️ Jadro ICCINT-80. Zodpovednosť sa presunie vždy; upozornenia len ak má nový manažér zapísaný
    // Telegram. Mlčanie by tu znamenalo, že hlásenia o projekte chodia ďalej starému manažérovi a
    // nikto sa to nedozvie — a odchod zo stránky by tú správu zmietol skôr, než by ju stihol prečítať.
    reassignProjectApiMock.mockResolvedValue({
      project: { ...project, created_by: "u-tibi" },
      notifications_follow_manager: false,
      notifications_blocked_reason: "tibi nemá zapísaný Telegram",
    });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await screen.findByRole("option", { name: "Tibor Rausch" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "u-tibi");
    await userEvent.click(screen.getByRole("button", { name: /zveriť projekt/i }));

    expect(await screen.findByText(/nemá zapísaný telegram/i)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("keď upozornenia prejdú, povie aj to — ticho by sa nedalo odlíšiť od nefunkčného tlačidla", async () => {
    reassignProjectApiMock.mockResolvedValue({
      project: { ...project, created_by: "u-tibi" },
      notifications_follow_manager: true,
      notifications_blocked_reason: null,
    });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await screen.findByRole("option", { name: "Tibor Rausch" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "u-tibi");
    await userEvent.click(screen.getByRole("button", { name: /zveriť projekt/i }));

    expect(await screen.findByText(/odteraz chodia novému manažérovi/i)).toBeInTheDocument();
  });
});

/**
 * ICCINT-88 — čo sa pri zakladaní nedokončilo, musí byť vidieť aj neskôr.
 *
 * Vetu o vynechaných krokoch kokpit zostavoval už predtým, ale žila len v odpovedi na založenie a
 * dialóg prevzatia po úspechu odchádza sem, na stránku projektu — takže ju nikto nestihol prečítať.
 * Správa, ktorú nikto neprečíta, je to isté ako ticho, a práve tomu má brániť.
 */
describe("ProjectDetailPage — záznam o nedokončenom zakladaní", () => {
  const veta =
    "Projekt bol prevzatý, takže sa doň nezasahovalo: nenastavovalo sa CI, ochrana vetvy ani skúšobné spustenie.";

  it("ukáže, čo sa pri zakladaní nedokončilo, aj pri obyčajnom otvorení stránky", async () => {
    getProjectApiMock.mockResolvedValue({ ...project, setup_warnings: [veta] });

    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByText(/Čo sa pri zakladaní nedokončilo/i)).toBeInTheDocument();
    expect(screen.getByText(veta)).toBeInTheDocument();
  });

  it("mlčí o projekte, kde všetko prebehlo", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);
    await screen.findByText(project.name);

    expect(screen.queryByText(/Čo sa pri zakladaní nedokončilo/i)).not.toBeInTheDocument();
  });
});
