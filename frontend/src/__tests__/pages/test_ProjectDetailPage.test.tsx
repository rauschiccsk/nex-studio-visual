/**
 * ProjectDetailPage — Fast-Fix Lane entry (F-009 §4 CR-B, CR-NS-095).
 *
 * The "Rýchla oprava" button opens a modal; submitting the directive POSTs to
 * /pipeline/fast-fix, pins the auto-created PATCH version into the active context
 * (project FIRST, then version), and navigates to the Vývoj board (/vyvoj,
 * renamed from /cockpit in CR-V2-019).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import { ApiError } from "@/services/api";

import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { FROM_DEAD_CODE_LABEL, FROM_UAT_LABEL } from "@/lib/fastFixHandoff";

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
  getNextVersionNumberApiMock,
  startFastFixApiMock,
  getDeadCodeApiMock,
  getProjectDedoProposalApiMock,
  getAppTemplateConversionApiMock,
  convertToAppTemplateApiMock,
  sendProjectDedoProposalApiMock,
  setSelectedProjectMock,
  setSelectedVersionMock,
  authStateMock,
  locationMock,
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
  getNextVersionNumberApiMock: vi.fn(),
  startFastFixApiMock: vi.fn(),
  getDeadCodeApiMock: vi.fn(),
  getProjectDedoProposalApiMock: vi.fn(),
  getAppTemplateConversionApiMock: vi.fn(),
  convertToAppTemplateApiMock: vi.fn(),
  sendProjectDedoProposalApiMock: vi.fn(),
  setSelectedProjectMock: vi.fn(),
  setSelectedVersionMock: vi.fn(),
  authStateMock: { user: { role: "ri", username: "admin", id: "u-admin" } as { role: string; username: string; id: string } | null },
  // DEV-57: where the manager arrived from — a link may carry `?rychla-oprava=1` or `#zadanie-od-deda`.
  locationMock: { search: "", hash: "" },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useParams: () => ({ slug: "demo" }),
    useLocation: () => ({
      pathname: "/projects/demo",
      search: locationMock.search,
      hash: locationMock.hash,
      key: "t",
      state: null,
    }),
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
  // ICCINT-152: stránka sa pýta, či Dedo nepripravil zadanie. Chýbajúca atrapa nie je „o jeden dopyt
  // menej“ — volanie `undefined` vyhodí chybu SYNCHRÓNNE a zhodí celé načítanie projektu.
  getProjectDedoProposalApi: getProjectDedoProposalApiMock,
  sendProjectDedoProposalApi: sendProjectDedoProposalApiMock,
  // DEV-164: the panel „Projekt s webom“ asks whether the project can switch to Web; it stays empty until it hears
  // back, so a question that never answers keeps it out of what these tests measure.
  getWebSwitchApi: vi.fn(() => new Promise(() => {})),
  switchToWebApi: vi.fn(),
  // DEV-254: the conversion onto the app template is offered above a new-version brief.
  getAppTemplateConversionApi: getAppTemplateConversionApiMock,
  convertToAppTemplateApi: convertToAppTemplateApiMock,
}));
vi.mock("@/services/api/users", () => ({ listUsersApi: listUsersApiMock }));
vi.mock("@/services/api/versions", () => ({
  listVersions: listVersionsMock,
  getVersion: getVersionMock,
  getNextVersionNumberApi: getNextVersionNumberApiMock,
}));
vi.mock("@/services/api/pipeline", () => ({ startFastFixApi: startFastFixApiMock }));
vi.mock("@/services/api/deadCode", () => ({ getDeadCodeApi: getDeadCodeApiMock }));
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
  private_network: false,
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
};

const patchVersion: Version = { ...baseVersion, id: "v2", version_number: "v0.6.1", name: "Rýchla oprava", status: "planned" };

beforeEach(() => {
  projectAssignmentsApiMock.mockResolvedValue([]);
  listUsersApiMock.mockResolvedValue({ items: [], total: 0 });
  vi.clearAllMocks();
  listProjectsApiMock.mockResolvedValue({ items: [project], total: 1, skip: 0, limit: 100 });
  getProjectApiMock.mockResolvedValue(project);
  getDeadCodeApiMock.mockResolvedValue({ findings: [], not_run: [], brief: null });
  deleteProjectApiMock.mockResolvedValue(undefined);
  listVersionsMock.mockResolvedValue([baseVersion]);
  getVersionMock.mockResolvedValue(patchVersion);
  getNextVersionNumberApiMock.mockResolvedValue({ version_number: "0.7.0" });
  startFastFixApiMock.mockResolvedValue({ version_id: "v2", board: { state: null, recent_messages: [] } });
  // Bežný stav: Dedo nič nenavrhol. Stránka sa má vtedy tváriť presne tak, ako sa tvárila doteraz.
  getProjectDedoProposalApiMock.mockResolvedValue(null);
  getAppTemplateConversionApiMock.mockResolvedValue({
    available: true,
    reason: null,
    templates: [{ id: "nsv-standard", label: "NSV štandard", description: "Rám ako v NSV." }],
    default_template: "nsv-standard",
  });
  // The ACCOUNT named admin — the ri ROLE no longer confers anything over a project (permissions.ts).
  authStateMock.user = { role: "ri", username: "admin", id: "u-admin" };
  locationMock.search = "";
  locationMock.hash = "";
});

async function importPage() {
  return (await import("@/pages/ProjectDetailPage")).default;
}

describe("ProjectDetailPage — Dedovo zadanie (ICCINT-152)", () => {
  it("ukáže zadanie, ktoré Dedo pripravil", async () => {
    getProjectDedoProposalApiMock.mockResolvedValue({
      id: "n-1",
      project_id: "p1",
      content: "Prijmi na spúšťacej adrese aj POST a lístok čítaj z tela.",
      proposed_action: "fast_fix",
      status: "proposed",
      created_at: "2026-09-25T10:00:00Z",
    });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByText(/Dedo .*pripravil zadanie/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Zadanie od Deda")).toHaveValue(
      "Prijmi na spúšťacej adrese aj POST a lístok čítaj z tela.",
    );
  });

  it("⚠️ zadanie je NAD zoznamom verzií — skôr než tlačidlo „Nová verzia“ (ICCINT-158)", async () => {
    // 26.09.2026 sa Director prišiel pozrieť, či zadanie od Deda dorazilo — a namiesto panela otvoril
    // formulár Nová verzia a začal ho vypĺňať ručne. Panel bol posledná karta stránky, pod červenou kartou
    // „Zmazať natrvalo"; ručná cesta je pritom hore v hlavičke zoznamu verzií. Zadanie ostalo neodoslané.
    //
    // Stráž meria PORADIE, nie prítomnosť. Keby merala len to, že panel na stránke je, prežila by aj návrat
    // na koniec stránky — teda presne tú chybu, kvôli ktorej vznikla.
    getProjectDedoProposalApiMock.mockResolvedValue({
      id: "n-1",
      project_id: "p1",
      content: "Povýš zdieľaný kit nex-shared z v0.8.1 na v0.19.0.",
      proposed_action: "new_version",
      status: "proposed",
      created_at: "2026-09-26T08:02:49Z",
      proposed_version_number: null,
      version_number: "0.20.0",
      unstarted_version_numbers: [],
    });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    const panel = await screen.findByText(/Dedo .*pripravil zadanie/i);
    const novaVerzia = screen.getByRole("button", { name: /^nová verzia$/i });

    expect(panel.compareDocumentPosition(novaVerzia) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("bez zadania vyzerá stránka presne tak, ako vyzerala doteraz", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await screen.findByRole("button", { name: /^rýchla oprava$/i });
    expect(screen.queryByText(/pripravil zadanie/i)).not.toBeInTheDocument();
  });

  it("⚠️ projekt sa načíta aj vtedy, keď dopyt na zadanie zlyhá", async () => {
    // Zadanie je pomoc navyše, nie podmienka toho, aby sa projekt dal otvoriť. Pri prvom zapojení
    // zhodil tento dopyt celé načítanie — a to synchrónne, takže `.catch` ho nezachytil.
    getProjectDedoProposalApiMock.mockRejectedValue(new Error("evidencia neodpovedala"));
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /^rýchla oprava$/i })).toBeInTheDocument();
    expect(screen.queryByText(/Nepodarilo sa načítať projekt/i)).not.toBeInTheDocument();
  });

  it("⚠️ a načíta sa aj vtedy, keď dopyt vyhodí chybu SYNCHRÓNNE", async () => {
    // Presne ten tvar, ktorý 25.09.2026 zhodil 17 stráží tejto stránky: atrapa dopyt nepoznala,
    // takže volanie `undefined` vyhodilo chybu ešte pred vznikom sľubu.
    getProjectDedoProposalApiMock.mockImplementation(() => {
      throw new TypeError("nie je to funkcia");
    });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /^rýchla oprava$/i })).toBeInTheDocument();
    expect(screen.queryByText(/Nepodarilo sa načítať projekt/i)).not.toBeInTheDocument();
  });
});

describe("ProjectDetailPage — Fast-Fix Lane (CR-NS-095)", () => {
  it("posts the directive, pins the patch version (project first), and opens the cockpit", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    // Entry button appears once the project + its (>0) versions load.
    const entry = await screen.findByRole("button", { name: /^rýchla oprava$/i });
    await userEvent.click(entry);

    await userEvent.type(screen.getByLabelText(/popis opravy/i), "Oprav preklep v sidebare.");
    // DEV-50: the fast fix says whether it fixes our own error (never billed) — required before it can start.
    expect(screen.getByRole("button", { name: /spustiť rýchlu opravu/i })).toBeDisabled();
    await userEvent.click(screen.getByRole("radio", { name: /oprava chyby v dodanom kóde/i }));
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(startFastFixApiMock).toHaveBeenCalledWith("p1", "Oprav preklep v sidebare.", "fix"),
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
    await userEvent.click(screen.getByRole("radio", { name: /zmena alebo nová práca/i }));
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    expect(await screen.findByText(/no semver version to patch/i)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("⚠️ DEV-175: refused for unsaved work, the dialog offers Uložiť / Zahodiť and opens no build", async () => {
    startFastFixApiMock.mockRejectedValue(
      new ApiError(409, "conflict", {
        detail: {
          message:
            "Projekt má neuložené zmeny (1). Ulož ich alebo zahoď — inak by kokpit nevedel, čo do novej verzie patrí. Verzia sa nezaložila.",
          project_id: "p1",
          unsaved_work: { clean: false, dirty_count: 1, files: [{ code: "M", path: "frontend/a.ts" }], truncated: false },
        },
      }),
    );
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^rýchla oprava$/i }));
    await userEvent.type(screen.getByLabelText(/popis opravy/i), "x");
    await userEvent.click(screen.getByRole("radio", { name: /oprava chyby v dodanom kóde/i }));
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    expect(await screen.findByText("Projekt má 1 zmena, ktoré neboli uložené")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uložiť ich" })).toBeInTheDocument();
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

describe("ProjectDetailPage — príchod zo zastaveného nasadenia (DEV-57)", () => {
  it("⚠️ odkaz „Spustiť rýchlu opravu“ otvorí dialóg Rýchla oprava a parameter zahodí", async () => {
    locationMock.search = "?rychla-oprava=1";
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("dialog", { name: "Rýchla oprava" })).toBeInTheDocument();
    expect(navigateMock).toHaveBeenCalledWith({ pathname: "/projects/demo", hash: "" }, { replace: true });
  });

  it("DEV-91: a brief prepared from a UAT test lands in the dialog saying where it came from, until he edits it", async () => {
    window.localStorage.setItem("nex.draft.rychla-oprava.demo", "V UAT nefunguje: Detail faktúry.");
    window.localStorage.setItem("nex.draft.rychla-oprava.demo.origin", "uat");
    locationMock.search = "?rychla-oprava=1";
    try {
      const ProjectDetailPage = await importPage();
      render(<ProjectDetailPage />);

      const dialog = await screen.findByRole("dialog", { name: "Rýchla oprava" });
      expect(within(dialog).getByLabelText("Popis opravy")).toHaveValue("V UAT nefunguje: Detail faktúry.");
      expect(within(dialog).getByText(FROM_UAT_LABEL)).toBeInTheDocument();
      fireEvent.change(within(dialog).getByLabelText("Popis opravy"), { target: { value: "Vlastný text." } });

      expect(within(dialog).queryByText(FROM_UAT_LABEL)).not.toBeInTheDocument();
      expect(window.localStorage.getItem("nex.draft.rychla-oprava.demo.origin")).toBeNull();
    } finally {
      window.localStorage.clear();
    }
  });

  it("DEV-156: „Upratať nepoužívaný kód“ opens the dialog on the prepared cleanup under his draft — nothing starts", async () => {
    window.localStorage.setItem("nex.draft.rychla-oprava.demo", "Môj rozpísaný text.");
    getDeadCodeApiMock.mockResolvedValue({
      findings: [
        { kind: "function", path: "app/x.py", name: "f", line: 3, sentence: "funkciu f (app/x.py:3) nikto nevolá" },
      ],
      not_run: [],
      brief: "Uprac kód, ktorý nikto nepoužíva.",
    });
    try {
      const ProjectDetailPage = await importPage();
      render(<ProjectDetailPage />);

      fireEvent.click(await screen.findByRole("button", { name: "Upratať nepoužívaný kód" }));

      const dialog = await screen.findByRole("dialog", { name: "Rýchla oprava" });
      expect(within(dialog).getByLabelText("Popis opravy")).toHaveValue(
        "Môj rozpísaný text.\n\nUprac kód, ktorý nikto nepoužíva.",
      );
      expect(within(dialog).getByText(FROM_DEAD_CODE_LABEL)).toBeInTheDocument();
      expect(window.localStorage.getItem("nex.draft.rychla-oprava.demo")).toBe(
        "Môj rozpísaný text.\n\nUprac kód, ktorý nikto nepoužíva.",
      );
      expect(startFastFixApiMock).not.toHaveBeenCalled();
    } finally {
      window.localStorage.clear();
    }
  });

  it("bez odkazu sa dialóg sám neotvorí", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /Rýchla oprava/ })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Rýchla oprava" })).not.toBeInTheDocument();
  });

  it("⚠️ projekt bez verzie nemá čo opraviť — dialóg sa neotvorí ani z odkazu", async () => {
    locationMock.search = "?rychla-oprava=1";
    listVersionsMock.mockResolvedValue([]);
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /Nová verzia/ })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Rýchla oprava" })).not.toBeInTheDocument();
  });

  it("⚠️ odkaz na Dedovo zadanie k nemu stránku posunie", async () => {
    locationMock.hash = "#zadanie-od-deda";
    getProjectDedoProposalApiMock.mockResolvedValue({
      id: "n-1",
      project_id: "p1",
      content: "Fix the unstable gate check.",
      proposed_action: "fast_fix",
      status: "proposed",
      created_at: "2026-10-10T15:12:02Z",
    });
    const scrolled = vi.fn();
    const original = Element.prototype.scrollIntoView; // jsdom has none — restored so no other test sees ours
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled(this.id);
    } as Element["scrollIntoView"];
    try {
      const ProjectDetailPage = await importPage();
      render(<ProjectDetailPage />);

      expect(await screen.findByLabelText("Zadanie od Deda")).toBeInTheDocument();
      await waitFor(() => expect(scrolled).toHaveBeenCalledWith("zadanie-od-deda"));
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});

describe("ProjectDetailPage — the next version's number comes from the cockpit (DEV-149)", () => {
  it("offers the number the cockpit gives, not one the page works out itself", async () => {
    // 0.7.0 is taken, so the cockpit skips it — the page alone would offer 0.7.0 after v0.6.0.
    getNextVersionNumberApiMock.mockResolvedValue({ version_number: "0.7.1" });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: "Pridať verziu 0.7.1" })).toBeInTheDocument();
    expect(getNextVersionNumberApiMock).toHaveBeenCalledWith("p1");
  });

  it("⚠️ the page loads even when the number cannot be asked for, and the button says so in plain words", async () => {
    getNextVersionNumberApiMock.mockRejectedValue(new Error("boom"));
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: "Pridať ďalšiu verziu" })).toBeInTheDocument();
    expect(screen.queryByText("Nepodarilo sa načítať projekt.")).not.toBeInTheDocument();
  });

  it("after a version is founded from Dedo's brief, the page offers the number after it (DEV-169)", async () => {
    // 21.10.2026 Career Asistent 1.1.0 was founded from Dedo's brief and the page went on offering
    // „Pridať verziu 1.1.0“ — the list was read again, the number was not.
    getProjectDedoProposalApiMock.mockResolvedValue({
      id: "n-1",
      project_id: "p1",
      content: "Kandidátska časť na verejnom webe.",
      proposed_action: "new_version",
      status: "proposed",
      created_at: "2026-10-21T15:06:34Z",
      proposed_version_number: "1.1.0",
      version_number: "1.1.0",
      unstarted_version_numbers: [],
    });
    getNextVersionNumberApiMock
      .mockResolvedValueOnce({ version_number: "1.1.0" })
      .mockResolvedValue({ version_number: "1.2.0" });
    sendProjectDedoProposalApiMock.mockResolvedValue({ version_id: "v9", started: false });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);
    await screen.findByRole("button", { name: "Pridať verziu 1.1.0" });

    fireEvent.click(await screen.findByRole("button", { name: "Založiť novú verziu" }));

    expect(await screen.findByRole("button", { name: "Pridať verziu 1.2.0" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pridať verziu 1.1.0" })).not.toBeInTheDocument();
    expect(sendProjectDedoProposalApiMock).toHaveBeenCalledWith(
      "p1",
      "n-1",
      "Kandidátska časť na verejnom webe.",
      null,
      "1.1.0",
    );
  });
});

describe("ProjectDetailPage — the conversion onto the app template above Dedo's brief (DEV-254)", () => {
  const brief = (proposed_action: "new_version" | "fast_fix") => ({
    id: "n-2",
    project_id: "p1",
    content: "NEX Knowledge 0.2.0 — prechod na šablónu aplikácie.",
    proposed_action,
    status: "proposed",
    created_at: "2026-10-29T10:00:00Z",
    proposed_version_number: "0.2.0",
    version_number: "0.2.0",
    unstarted_version_numbers: [],
  });

  it("a new version from the brief is offered the conversion first, and it goes once done", async () => {
    getProjectDedoProposalApiMock.mockResolvedValue(brief("new_version"));
    convertToAppTemplateApiMock.mockResolvedValue({ template: "nsv-standard", kit_version: "0.3.0" });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Previesť na NSV štandard" }));

    await waitFor(() => expect(convertToAppTemplateApiMock).toHaveBeenCalledWith("p1", "nsv-standard", false));
    await waitFor(() =>
      expect(screen.queryByText("Previesť projekt na šablónu aplikácie")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Založiť novú verziu" })).toBeInTheDocument();
  });

  it("a fast fix from the brief is not offered the conversion", async () => {
    getProjectDedoProposalApiMock.mockResolvedValue(brief("fast_fix"));
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByText("NEX Knowledge 0.2.0 — prechod na šablónu aplikácie.")).toBeInTheDocument();
    expect(getAppTemplateConversionApiMock).not.toHaveBeenCalled();
  });

  it("without a brief the page does not ask about the conversion", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect((await screen.findAllByText("Demo")).length).toBeGreaterThan(0);
    await waitFor(() => expect(getProjectDedoProposalApiMock).toHaveBeenCalled());
    expect(getAppTemplateConversionApiMock).not.toHaveBeenCalled();
  });
});
