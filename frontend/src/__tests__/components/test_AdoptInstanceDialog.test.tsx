/**
 * Prevzatie ručne písanej inštalácie z kokpitu (ICCINT-102).
 *
 * Director 10.09.2026: *„Pracujeme nad vývojovým ekosystémom, ktorý má zabezpečiť KOMPLETNÝ workflow
 * od návrhu až po nasadenie na UAT a na PROD. Také terminálové príkazy nie sú pre mňa riešenie.“*
 *
 * Poistka nad ručne písanými priečinkami zostáva — mení sa len to, že rozhodnutie sa dá urobiť tam,
 * kde človek pracuje, a s väčšou rozvahou než v termináli: s náhľadom, s odpísaním frázy a so
 * záznamom. Terminálová cesta nemala ani jedno.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

const { getAdoptionPreviewMock, adoptInstanceMock } = vi.hoisted(() => ({
  getAdoptionPreviewMock: vi.fn(),
  adoptInstanceMock: vi.fn(),
}));

vi.mock("@/services/api/deploy", () => ({
  getAdoptionPreview: getAdoptionPreviewMock,
  adoptInstance: adoptInstanceMock,
}));

const RUCNA = {
  instance_dir: "/opt/uat/mager/nex-manager",
  exists: true,
  already_ours: false,
  set_aside: [
    [".env", ".env.pre-nex-studio"],
    ["docker-compose.yml", "docker-compose.yml.pre-nex-studio"],
  ] as [string, string][],
  untouched: [],
  running_containers: ["uat-mager-manager-backend", "uat-mager-manager-db"],
  confirmation_phrase: "mager/nex-manager",
};

beforeEach(() => {
  vi.clearAllMocks();
  getAdoptionPreviewMock.mockResolvedValue(RUCNA);
  adoptInstanceMock.mockResolvedValue({ ok: true, event: { detail: null }, url: null, warnings: [] });
});

async function otvor(props: Partial<Record<string, unknown>> = {}) {
  const Dialog = (await import("@/components/deploy/AdoptInstanceDialog")).default;
  const onClose = vi.fn();
  const onAdopted = vi.fn();
  render(
    <Dialog
      customerId="c1"
      customerName="MÁGERSTAV s.r.o."
      environment="uat"
      versionNumber="1.1.0"
      onClose={onClose}
      onAdopted={onAdopted}
      {...props}
    />,
  );
  return { onClose, onAdopted };
}

describe("Prevzatie inštalácie — náhľad pred rozhodnutím", () => {
  it("⚠️ povie, ČO sa odloží a čoho sa to nedotkne — nie len že „sa niečo prepíše“", async () => {
    await otvor();

    expect(await screen.findByText(/opt\/uat\/mager\/nex-manager/)).toBeInTheDocument();
    expect(screen.getByText(/docker-compose\.yml → docker-compose\.yml\.pre-nex-studio/)).toBeInTheDocument();
    expect(screen.getByText(/\.env → \.env\.pre-nex-studio/)).toBeInTheDocument();
  });

  it("⚠️ povie, čo z toho priečinka PRÁVE BEŽÍ — najsilnejší dôkaz, že to nie je opustený zvyšok", async () => {
    await otvor();

    expect(await screen.findByText(/uat-mager-manager-backend/)).toBeInTheDocument();
  });

  it("sľúbi, že prihlasovacie údaje a dáta zostanú", async () => {
    await otvor();

    expect(await screen.findByText(/zachovajú sa/i)).toBeInTheDocument();
  });
});

describe("Prevzatie inštalácie — potvrdenie sa odpisuje, neodklikáva", () => {
  it("⚠️ bez odpísanej frázy sa prevziať nedá", async () => {
    await otvor();
    await screen.findByLabelText(/odpíš/i);

    const tlacidlo = screen.getByRole("button", { name: /prevziať a nasadiť/i });
    expect(tlacidlo).toBeDisabled();
    expect(tlacidlo).toHaveAttribute("title", expect.stringContaining("mager/nex-manager"));
  });

  it("nesprávna fráza nestačí — priečinok sa u troch zákazníkov volá rovnako", async () => {
    await otvor();
    await userEvent.type(await screen.findByLabelText(/odpíš/i), "nex-manager");

    expect(screen.getByRole("button", { name: /prevziať a nasadiť/i })).toBeDisabled();
  });

  it("po správnej fráze sa prevezme — a pošle sa presne tá fráza", async () => {
    const { onAdopted } = await otvor();
    await userEvent.type(await screen.findByLabelText(/odpíš/i), "mager/nex-manager");
    await userEvent.click(screen.getByRole("button", { name: /prevziať a nasadiť/i }));

    await waitFor(() => expect(adoptInstanceMock).toHaveBeenCalled());
    expect(adoptInstanceMock).toHaveBeenCalledWith("c1", {
      version_number: "1.1.0",
      environment: "uat",
      confirm: "mager/nex-manager",
    });
    expect(onAdopted).toHaveBeenCalled();
  });
});

describe("Prevzatie inštalácie — keď niet čo preberať", () => {
  it("náš vlastný priečinok sa nepreberá a povie sa to", async () => {
    getAdoptionPreviewMock.mockResolvedValue({ ...RUCNA, already_ours: true });
    await otvor();

    expect(await screen.findByText(/už NEX Studio spravuje/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /prevziať a nasadiť/i })).not.toBeInTheDocument();
  });

  it("neexistujúci priečinok nasmeruje na bežné Nasadiť", async () => {
    getAdoptionPreviewMock.mockResolvedValue({ ...RUCNA, exists: false, set_aside: [] });
    await otvor();

    expect(await screen.findByText(/neexistuje/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /prevziať a nasadiť/i })).not.toBeInTheDocument();
  });

  it("zlyhanie prevzatia je VIDIEŤ — nie tiché zavretie dialógu", async () => {
    adoptInstanceMock.mockRejectedValue(new Error("engine odmietol"));
    const { onClose } = await otvor();
    await userEvent.type(await screen.findByLabelText(/odpíš/i), "mager/nex-manager");
    await userEvent.click(screen.getByRole("button", { name: /prevziať a nasadiť/i }));

    expect(await screen.findByText(/prevzatie zlyhalo/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
