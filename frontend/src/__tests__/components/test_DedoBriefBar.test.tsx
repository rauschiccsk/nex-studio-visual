/**
 * DedoBriefBar (ICCINT-152) — zadanie od Deda pre prácu, ktorá sa ešte NEZAČALA.
 *
 * 25.09.2026 prestalo na MÁGERSTAVE fungovať spúšťanie NEX Inboxu z NEX Managera. Oprava patrila do
 * NEX Inboxu a Director požiadal: „zapíš to zadanie do kokpitu ako návrh." Nešlo to — návrh sa vedel
 * pripnúť len na bežiacu stavbu, teda práve vtedy, keď už zadanie netreba. Text mu Dedo musel podať do
 * ruky, aby ho pri spúšťaní rýchlej opravy vložil.
 *
 * ⚠️ Čo tieto stráže NEDOVOLIA zmeniť:
 *  - panel sa NEVYKRESLÍ, keď zadanie nie je (inak by bola na stránke stála pozvánka spustiť nič);
 *  - je vidieť, že text napísal DEDO a že sa zatiaľ nič nezačalo;
 *  - okienko je editovateľné a použije sa to, čo je v ňom — nie pôvodné znenie;
 *  - tlačidlo nesie identifikátor zadania, ktoré má Manažér na obrazovke;
 *  - „nová verzia" hovorí, že sa NIČ nerozbehne — rozdiel medzi pripraveným a bežiacim je to jediné,
 *    čo sa tu rozhoduje;
 *  - zamietnuť je rovnocenná možnosť a nespustí nič.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DedoBriefBar from "@/components/projects/DedoBriefBar";
import {
  getProjectDedoProposalApi,
  rejectProjectDedoProposalApi,
  sendProjectDedoProposalApi,
  type DedoProjectProposal,
} from "@/services/api/projects";
import { ApiError } from "@/services/api";

vi.mock("@/services/api/projects", () => ({
  getProjectDedoProposalApi: vi.fn(),
  rejectProjectDedoProposalApi: vi.fn(),
  sendProjectDedoProposalApi: vi.fn(),
  // DEV-175: Uložiť / Zahodiť of a refused founding
  commitGitApi: vi.fn(),
  discardGitApi: vi.fn(),
  getGitStatusApi: vi.fn(),
}));

const ZADANIE =
  "NEX Manager od 1.2.0 posiela spúšťací lístok telom požiadavky, Inbox má tú adresu len na GET. " +
  "Prijmi na tej adrese aj POST a lístok čítaj z tela.";

const PROJEKT = "p-1";

function navrh(over: Partial<DedoProjectProposal> = {}): DedoProjectProposal {
  return {
    id: "n-1",
    project_id: PROJEKT,
    content: ZADANIE,
    proposed_action: "fast_fix",
    status: "proposed",
    created_at: "2026-09-25T10:00:00Z",
    proposed_version_number: null,
    version_number: null,
    ...over,
  };
}

function vykresli(proposal: DedoProjectProposal | null) {
  const onProposal = vi.fn();
  const onVersion = vi.fn();
  render(
    <DedoBriefBar projectId={PROJEKT} proposal={proposal} onProposal={onProposal} onVersion={onVersion} />,
  );
  return { onProposal, onVersion };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("Panel sa ukáže len vtedy, keď je čo rozhodnúť", () => {
  it("bez zadania nevykreslí nič", () => {
    const { container } = render(
      <DedoBriefBar projectId={PROJEKT} proposal={null} onProposal={vi.fn()} onVersion={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("povie, že text napísal Dedo a že sa zatiaľ nič nezačalo", () => {
    vykresli(navrh());

    expect(screen.getByText(/Dedo .*pripravil zadanie/i)).toBeInTheDocument();
    expect(screen.getByText(/zatiaľ sa NIČ nezačalo/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Zadanie od Deda")).toHaveValue(ZADANIE);
  });
});

describe("Čo tlačidlo spraví, je povedané ako dôsledok", () => {
  it("rýchla oprava hovorí, že sa verzia vytvorí a rozbehne", () => {
    vykresli(navrh());

    expect(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i })).toBeInTheDocument();
    expect(screen.getByText(/rozbehne odľahčená linka/i)).toBeInTheDocument();
  });

  it("nová verzia hovorí, že sa NIČ nerozbehne", () => {
    vykresli(navrh({ proposed_action: "new_version" }));

    expect(screen.getByRole("button", { name: /Založiť novú verziu/i })).toBeInTheDocument();
    expect(screen.getByText(/Nič sa nerozbehne/i)).toBeInTheDocument();
  });
});

describe("Rozhoduje Manažér", () => {
  it("spustenie pošle identifikátor zadania, ktoré má na obrazovke", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockResolvedValue({ version_id: "v-9", started: true });
    const { onVersion } = vykresli(navrh());

    fireEvent.click(screen.getByLabelText(/Oprava chyby v dodanom kóde/)); // DEV-56: the work kind first
    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", ZADANIE, "fix"),
    );
    await waitFor(() => expect(onVersion).toHaveBeenCalledWith("v-9", true));
  });

  it("použije sa to, čo je v okienku — nie pôvodné znenie", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockResolvedValue({ version_id: "v-9", started: true });
    vykresli(navrh());
    const upravene = ZADANIE + " Najprv over, či to na UAT naozaj padá.";

    fireEvent.change(screen.getByLabelText("Zadanie od Deda"), { target: { value: upravene } });
    fireEvent.click(screen.getByLabelText(/Oprava chyby v dodanom kóde/)); // DEV-56: the work kind first
    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", upravene, "fix"),
    );
  });

  it("úpravu povie nahlas", () => {
    vykresli(navrh());

    fireEvent.change(screen.getByLabelText("Zadanie od Deda"), { target: { value: "iné znenie" } });

    expect(screen.getByText(/použije sa tvoje znenie/i)).toBeInTheDocument();
  });

  it("zamietnuť je rovnocenná možnosť a nespustí nič", async () => {
    vi.mocked(rejectProjectDedoProposalApi).mockResolvedValue({ status: "rejected" });
    const { onProposal, onVersion } = vykresli(navrh());

    fireEvent.click(screen.getByRole("button", { name: /Zamietnuť zadanie/i }));

    await waitFor(() => expect(rejectProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1"));
    await waitFor(() => expect(onProposal).toHaveBeenCalledWith(null));
    expect(sendProjectDedoProposalApi).not.toHaveBeenCalled();
    expect(onVersion).not.toHaveBeenCalled();
  });
});

describe("Keď sa zadanie zmenilo pod rukami", () => {
  it("odmietnutie ukáže dôvod a načíta to nové", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockRejectedValue(
      new ApiError(409, "Dedo medzitým napísal novší návrh; tento už neplatí."),
    );
    const novsie = navrh({ id: "n-2", content: "novšie zadanie" });
    vi.mocked(getProjectDedoProposalApi).mockResolvedValue(novsie);
    const { onProposal, onVersion } = vykresli(navrh());

    fireEvent.click(screen.getByLabelText(/Oprava chyby v dodanom kóde/)); // DEV-56: the work kind first
    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i }));

    expect(await screen.findByText(/napísal novší návrh/i)).toBeInTheDocument();
    await waitFor(() => expect(onProposal).toHaveBeenCalledWith(novsie));
    expect(onVersion).not.toHaveBeenCalled();
  });
});

describe("Druh práce pri rýchlej oprave (DEV-56)", () => {
  it("⚠️ rýchlu opravu zo zadania nespustí, kým Manažér neurčí druh práce — a ten pošle", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockResolvedValue({ version_id: "v-9", started: true });
    vykresli(navrh());
    const start = screen.getByRole("button", { name: /Spustiť rýchlu opravu/i });

    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(sendProjectDedoProposalApi).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText(/Zmena alebo nová práca/));
    expect(start).toBeEnabled();
    fireEvent.click(start);
    await waitFor(() => expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", ZADANIE, "change"));
  });

  it("nová verzia zo zadania sa na druh práce nepýta", () => {
    // DEV-149: a new-version brief always arrives with the number it founds the version with.
    vykresli(navrh({ proposed_action: "new_version", version_number: "1.10.0" }));

    expect(screen.queryByText("Druh práce")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Založiť novú verziu/i })).toBeEnabled();
  });
});

describe("Číslo novej verzie Manažér vidí pred založením (DEV-149)", () => {
  // 19.10.2026: zadanie navrhovalo NEX Inbox 1.10.0, kokpit založil 2.0.0 — číslo pred klikom nebolo nikde vidieť.
  const novaVerzia = (over: Partial<DedoProjectProposal> = {}) =>
    navrh({ proposed_action: "new_version", proposed_version_number: "1.10.0", version_number: "1.10.0", ...over });

  it("ukáže číslo, s ktorým verzia vznikne, a založí ju s ním", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockResolvedValue({ version_id: "v-9", started: false });
    vykresli(novaVerzia());

    expect(screen.getByLabelText("Číslo verzie")).toHaveValue("1.10.0");
    fireEvent.click(screen.getByRole("button", { name: /Založiť novú verziu/i }));

    await waitFor(() =>
      expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", ZADANIE, null, "1.10.0"),
    );
  });

  it("založí ju s číslom, ktoré Manažér zmenil", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockResolvedValue({ version_id: "v-9", started: false });
    vykresli(novaVerzia());

    fireEvent.change(screen.getByLabelText("Číslo verzie"), { target: { value: "2.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: /Založiť novú verziu/i }));

    await waitFor(() =>
      expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", ZADANIE, null, "2.0.0"),
    );
  });

  it("s číslom v zlom tvare verziu nezaloží a povie prečo", () => {
    vykresli(novaVerzia());

    fireEvent.change(screen.getByLabelText("Číslo verzie"), { target: { value: "1.10" } });

    expect(screen.getByRole("button", { name: /Založiť novú verziu/i })).toBeDisabled();
    expect(screen.getByText("v tvare 1.2.3")).toBeInTheDocument();
  });

  it("rýchla oprava vlastné číslo neukazuje — určuje ho pravidlo opravy", () => {
    vykresli(navrh());

    expect(screen.queryByLabelText("Číslo verzie")).not.toBeInTheDocument();
  });
});

// DEV-175: the engine's refusal to found a version over unsaved work — the same body the backend sends.
function unsavedWorkRefusal(): ApiError {
  return new ApiError(409, "conflict", {
    detail: {
      message: "Projekt má neuložené zmeny (1). Ulož ich alebo zahoď — inak by kokpit nevedel, čo do novej verzie patrí. Verzia sa nezaložila.",
      project_id: "p-1",
      unsaved_work: { clean: false, dirty_count: 1, files: [{ code: "M", path: "frontend/a.ts" }], truncated: false },
    },
  });
}

describe("Neuložené zmeny projektu (DEV-175)", () => {
  it.each([
    ["fast_fix", /Spustiť rýchlu opravu/i],
    ["new_version", /Založiť novú verziu/i],
  ])("⚠️ %s: odmietnutie ponúkne Uložiť / Zahodiť a zadanie ostane otvorené", async (action, button) => {
    vi.mocked(sendProjectDedoProposalApi).mockRejectedValue(unsavedWorkRefusal());
    const { onProposal, onVersion } = vykresli(navrh({ proposed_action: action, version_number: "1.1.0" }));

    if (action === "fast_fix") fireEvent.click(screen.getByLabelText(/Oprava chyby v dodanom kóde/));
    fireEvent.click(screen.getByRole("button", { name: button }));

    expect(await screen.findByText("Projekt má 1 zmena, ktoré neboli uložené")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uložiť ich" })).toBeInTheDocument();
    // Nie „zadanie sa zmenilo“: to by ho načítalo znova a Manažér by nevedel, čo sa stalo.
    expect(getProjectDedoProposalApi).not.toHaveBeenCalled();
    expect(onProposal).not.toHaveBeenCalled();
    expect(onVersion).not.toHaveBeenCalled();
  });
});
