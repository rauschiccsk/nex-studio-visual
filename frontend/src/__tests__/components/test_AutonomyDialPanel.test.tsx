/**
 * Component tests for AutonomyDialPanel — the Miera autonómie dial surface in
 * ⚙️ Nastavenia (NEX Studio v2.0.0, CR-V2-030 / SET-1).
 *
 * Locks in the CR-030 gate criteria for the dial surface:
 *   - the 4 presets render (plna / len_na_konci / pri_klucovych_bodoch / po_kazdej_faze);
 *   - the TWO always-outside-the-dial exceptions are documented (Špecifikácia approval + deploy);
 *   - selecting + saving calls onSave with the chosen level;
 *   - read-only (canEdit=false) disables the picker + hides the save button.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { AutonomyDialPanel } from "@/components/settings/AutonomyDialPanel";

function renderPanel(overrides: Partial<Parameters<typeof AutonomyDialPanel>[0]> = {}) {
  const onSave = overrides.onSave ?? vi.fn().mockResolvedValue(undefined);
  render(
    <AutonomyDialPanel
      value={overrides.value ?? "plna"}
      isDefault={overrides.isDefault ?? true}
      updatedByUsername={overrides.updatedByUsername ?? null}
      updatedAt={overrides.updatedAt ?? null}
      canEdit={overrides.canEdit ?? true}
      onSave={onSave}
      loading={overrides.loading ?? false}
      loadError={overrides.loadError ?? ""}
    />,
  );
  return { onSave };
}

describe("AutonomyDialPanel", () => {
  it("renders all 4 presets", () => {
    renderPanel();
    expect(screen.getByText("Plná autonómia")).toBeInTheDocument();
    expect(screen.getByText("Len na konci")).toBeInTheDocument();
    expect(screen.getByText("Pri kľúčových bodoch")).toBeInTheDocument();
    expect(screen.getByText("Po každej fáze")).toBeInTheDocument();
    // exactly 4 radio cards
    expect(screen.getAllByRole("radio")).toHaveLength(4);
  });

  it("documents the two always-outside-the-dial exceptions", () => {
    renderPanel();
    expect(screen.getByText("Vždy mimo tohto nastavenia")).toBeInTheDocument();
    expect(screen.getByText("Schválenie špecifikácie")).toBeInTheDocument();
    expect(screen.getByText("Nasadenie (UAT / PROD)")).toBeInTheDocument();
  });

  it("marks the current value as checked", () => {
    renderPanel({ value: "pri_klucovych_bodoch" });
    const radios = screen.getAllByRole("radio");
    const checked = radios.filter((r) => r.getAttribute("aria-checked") === "true");
    expect(checked).toHaveLength(1);
    expect(checked[0]).toHaveTextContent("Pri kľúčových bodoch");
  });

  it("saves the selected level via onSave", async () => {
    const { onSave } = renderPanel({ value: "plna" });
    fireEvent.click(screen.getByText("Po každej fáze"));
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("po_kazdej_faze"));
  });

  it("disables the picker and hides the save button when read-only", () => {
    renderPanel({ canEdit: false });
    for (const r of screen.getAllByRole("radio")) {
      expect(r).toBeDisabled();
    }
    expect(screen.queryByRole("button", { name: /Uložiť/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Iba na čítanie/)).toBeInTheDocument();
  });

  it("degrades an unrecognised stored value to plna", () => {
    renderPanel({ value: "garbage" });
    const checked = screen
      .getAllByRole("radio")
      .filter((r) => r.getAttribute("aria-checked") === "true");
    expect(checked).toHaveLength(1);
    expect(checked[0]).toHaveTextContent("Plná autonómia");
  });
});
