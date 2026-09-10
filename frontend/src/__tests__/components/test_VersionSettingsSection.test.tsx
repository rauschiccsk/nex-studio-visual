/**
 * Nastavenia verzie — hodnota zadaná pri zakladaní sa dá opraviť (ICCINT-100).
 *
 * Director: „Nemám možnosť (aspoň som nenašiel) ako premenovať verziu.“ Nenašiel preto, že tam nebola:
 * `updateVersion` volalo jediné miesto v celom rozhraní — formulár novej verzie pri druhom pokuse
 * o uloženie. Stránka verzie tie polia len vypisovala.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import type { Version } from "@/types/version";

const { updateVersionMock, getVersionSettingsMock } = vi.hoisted(() => ({
  updateVersionMock: vi.fn(),
  getVersionSettingsMock: vi.fn(),
}));

vi.mock("@/services/api/versions", () => ({
  updateVersion: updateVersionMock,
  getVersionSettings: getVersionSettingsMock,
}));

const version: Version = {
  id: "v1",
  project_id: "p1",
  version_number: "1.1.0",
  name: "Inštalovateľná appka",
  status: "done",
  description: "Inštalovateľná appka PWA",
  target_date: null,
  release_date: null,
  created_at: "2026-09-09T00:00:00Z",
  updated_at: "2026-09-09T00:00:00Z",
} as unknown as Version;

const ZAMKNUTE =
  "Číslo verzie sa už nedá zmeniť — podľa neho sa volá priečinok s dokumentmi " +
  "(docs/specs/versions/v1.1.0/) a ten už existuje.";

beforeEach(() => {
  vi.clearAllMocks();
  getVersionSettingsMock.mockResolvedValue({ version_number_lock_reason: null });
  updateVersionMock.mockImplementation((_id: string, d: Record<string, unknown>) =>
    Promise.resolve({ ...version, ...d }),
  );
});

async function renderPanel(onSaved = vi.fn(), canEdit = true) {
  const Panel = (await import("@/components/version/VersionSettingsSection")).default;
  render(<Panel version={version} canEdit={canEdit} onSaved={onSaved} />);
  return onSaved;
}

describe("Nastavenia verzie", () => {
  it("⚠️ dovolí premenovať verziu po dokončení stavby — presne to, čo nešlo", async () => {
    const onSaved = await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    const nazov = screen.getByLabelText(/názov/i);
    await userEvent.clear(nazov);
    await userEvent.type(nazov, "Inštalovateľná aplikácia PWA");
    await userEvent.click(screen.getByRole("button", { name: /uložiť/i }));

    await waitFor(() => expect(updateVersionMock).toHaveBeenCalled());
    expect(updateVersionMock).toHaveBeenCalledWith("v1", { name: "Inštalovateľná aplikácia PWA" });
    expect(onSaved).toHaveBeenCalled();
  });

  it("neposiela nič, keď sa nič nezmenilo", async () => {
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));
    await userEvent.click(screen.getByRole("button", { name: /uložiť/i }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /^uložiť$/i })).not.toBeInTheDocument());
    expect(updateVersionMock).not.toHaveBeenCalled();
  });

  it("číslo verzie sa dá opraviť, kým podľa neho nič nevzniklo", async () => {
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    expect(screen.getByLabelText(/číslo verzie/i)).toBeEnabled();
  });

  it("⚠️ zamknuté číslo povie PREČO — inak človek hľadá chybu u seba", async () => {
    getVersionSettingsMock.mockResolvedValue({ version_number_lock_reason: ZAMKNUTE });
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    expect(screen.getByLabelText(/číslo verzie/i)).toBeDisabled();
    expect(screen.getByText(/priečinok s dokumentmi/i)).toBeInTheDocument();
  });

  it("zámok sa týka IBA čísla — názov a dátum ostávajú otvorené", async () => {
    getVersionSettingsMock.mockResolvedValue({ version_number_lock_reason: ZAMKNUTE });
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    expect(screen.getByLabelText(/názov/i)).toBeEnabled();
    expect(screen.getByLabelText(/cieľový dátum/i)).toBeEnabled();
  });

  it("keď sa stav zámku nedá zistiť, pole sa ZAMKNE — nie otvorí", async () => {
    getVersionSettingsMock.mockRejectedValue(new Error("engine mlčí"));
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    // Otvorené pole nad neznámym stavom sľubuje zmenu, ktorú engine aj tak odmietne.
    expect(screen.getByLabelText(/číslo verzie/i)).toBeDisabled();
    expect(screen.getByText(/nepodarilo sa zistiť/i)).toBeInTheDocument();
  });

  it("zlyhanie uloženia je VIDIEŤ — nie tichý návrat do zobrazenia", async () => {
    updateVersionMock.mockRejectedValue(new Error("engine odmietol"));
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));
    await userEvent.type(screen.getByLabelText(/názov/i), " X");
    await userEvent.click(screen.getByRole("button", { name: /uložiť/i }));

    expect(await screen.findByText(/nepodarilo uložiť/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť/i })).toBeInTheDocument();
  });

  it("⚠️ popis sa dá opraviť tiež — vzniká z Zadania a po premenovaní zostával starý (ICCINT-101)", async () => {
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    const popis = screen.getByLabelText(/^popis$/i);
    await userEvent.clear(popis);
    await userEvent.type(popis, "Inštalovateľná aplikácia PWA");
    await userEvent.click(screen.getByRole("button", { name: /uložiť/i }));

    await waitFor(() => expect(updateVersionMock).toHaveBeenCalled());
    expect(updateVersionMock).toHaveBeenCalledWith("v1", { description: "Inštalovateľná aplikácia PWA" });
  });

  it("zámok čísla verzie sa popisu netýka", async () => {
    getVersionSettingsMock.mockResolvedValue({ version_number_lock_reason: ZAMKNUTE });
    await renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /upraviť/i }));

    expect(screen.getByLabelText(/^popis$/i)).toBeEnabled();
  });

  it("povie, čo popis vlastne je — inak si ho ľahko pomýliť so Zadaním", async () => {
    await renderPanel();

    expect(screen.getByText(/súhrn v zozname verzií/i)).toBeInTheDocument();
    expect(screen.getByText(/samotné Zadanie sa tým nemení/i)).toBeInTheDocument();
  });

  it("kto projekt neriadi, upravovať nemôže — a dozvie sa prečo", async () => {
    await renderPanel(vi.fn(), false);

    const tlacidlo = await screen.findByRole("button", { name: /upraviť/i });
    expect(tlacidlo).toBeDisabled();
    expect(tlacidlo).toHaveAttribute("title", expect.stringMatching(/vlastník|správca/i));
  });
});
