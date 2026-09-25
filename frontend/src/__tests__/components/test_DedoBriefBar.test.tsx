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

    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", ZADANIE),
    );
    await waitFor(() => expect(onVersion).toHaveBeenCalledWith("v-9", true));
  });

  it("použije sa to, čo je v okienku — nie pôvodné znenie", async () => {
    vi.mocked(sendProjectDedoProposalApi).mockResolvedValue({ version_id: "v-9", started: true });
    vykresli(navrh());
    const upravene = ZADANIE + " Najprv over, či to na UAT naozaj padá.";

    fireEvent.change(screen.getByLabelText("Zadanie od Deda"), { target: { value: upravene } });
    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(sendProjectDedoProposalApi).toHaveBeenCalledWith(PROJEKT, "n-1", upravene),
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

    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/i }));

    expect(await screen.findByText(/napísal novší návrh/i)).toBeInTheDocument();
    await waitFor(() => expect(onProposal).toHaveBeenCalledWith(novsie));
    expect(onVersion).not.toHaveBeenCalled();
  });
});
