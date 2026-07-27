/**
 * Component tests for TokenPricesPanel — the compact model price table in
 * ⚙️ Nastavenia → Systém.
 *
 * The defect that matters here is a WRONG KEY MAPPING: the table shows a model
 * name but writes another family's setting, which mis-prices the whole Náklady
 * screen silently. So the save test asserts the exact (key, value) pair for all
 * EIGHT keys, with a distinct value per cell — a swapped in/out or a swapped
 * family cannot pass.
 *
 * Also locked in: the three model rows render their stored values, canEdit=false
 * disables every input, and the flat fallback row is present + labelled (an
 * invisible fallback price is how a silent mis-pricing happens).
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { TokenPricesPanel } from "@/components/settings/TokenPricesPanel";
import { TOKEN_PRICE_KEYS } from "@/components/settings/tokenPrices";
import type { SystemSettingRead } from "@/types/system_setting";

/** A stored price row, shaped like the backend serialisation. */
function setting(key: string, value: string, overrides: Partial<SystemSettingRead> = {}): SystemSettingRead {
  return {
    key,
    value,
    value_type: "float",
    description: null,
    label: null,
    unit: "€ / mil. tokenov",
    updated_at: null,
    updated_by: null,
    updated_by_username: null,
    is_default: true,
    ...overrides,
  };
}

/** The save handler's signature, mirroring the shared SystemSettingsPanel. */
type SaveFn = (key: string, value: string) => Promise<SystemSettingRead>;

// A distinct stored value per key so a swapped mapping is visible in the DOM.
const STORED: Record<string, string> = {
  api_price_input_per_mtok_haiku: "0.8",
  api_price_output_per_mtok_haiku: "4",
  api_price_input_per_mtok_sonnet: "2.7",
  api_price_output_per_mtok_sonnet: "13.5",
  api_price_input_per_mtok_opus: "13.6",
  api_price_output_per_mtok_opus: "68",
  api_price_input_per_mtok: "1.1",
  api_price_output_per_mtok: "5.5",
};

/** Stored value for a key (STORED covers all eight; indexing is checked). */
function storedOf(key: string): string {
  const v = STORED[key];
  if (v === undefined) throw new Error(`test fixture missing a value for ${key}`);
  return v;
}

function renderPanel(
  overrides: {
    settings?: SystemSettingRead[];
    canEdit?: boolean;
    onSave?: SaveFn;
    loading?: boolean;
    loadError?: string;
  } = {},
) {
  const onSave: SaveFn =
    overrides.onSave ??
    vi.fn((key: string, value: string) => Promise.resolve(setting(key, value, { is_default: false })));
  const settings = overrides.settings ?? TOKEN_PRICE_KEYS.map((k) => setting(k, storedOf(k)));
  render(
    <TokenPricesPanel
      settings={settings}
      canEdit={overrides.canEdit ?? true}
      onSave={onSave}
      loading={overrides.loading ?? false}
      loadError={overrides.loadError ?? ""}
    />,
  );
  return { onSave };
}

/** The input for one cell, by its accessible name. */
function cell(direction: "vstup" | "výstup", model: string): HTMLInputElement {
  return screen.getByLabelText(`Cena ${direction} — ${model}`) as HTMLInputElement;
}

describe("TokenPricesPanel", () => {
  it("renders the three model rows in the Director's order with their stored values", () => {
    renderPanel();

    // Row order: Haiku, Sonnet, Opus.
    const rowHeaders = screen.getAllByRole("rowheader").map((h) => h.textContent ?? "");
    expect(rowHeaders[0]).toContain("Haiku");
    expect(rowHeaders[1]).toContain("Sonnet");
    expect(rowHeaders[2]).toContain("Opus");

    expect(cell("vstup", "Haiku")).toHaveValue(0.8);
    expect(cell("výstup", "Haiku")).toHaveValue(4);
    expect(cell("vstup", "Sonnet")).toHaveValue(2.7);
    expect(cell("výstup", "Sonnet")).toHaveValue(13.5);
    expect(cell("vstup", "Opus")).toHaveValue(13.6);
    expect(cell("výstup", "Opus")).toHaveValue(68);
  });

  it("renders both price columns as € per million tokens", () => {
    renderPanel();
    const cols = screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");
    expect(cols[0]).toContain("Model");
    expect(cols[1]).toContain("Cena vstup");
    expect(cols[1]).toContain("€ / mil. tokenov");
    expect(cols[2]).toContain("Cena výstup");
    expect(cols[2]).toContain("€ / mil. tokenov");
  });

  it("shows the flat fallback row, labelled and explained — never hidden", () => {
    renderPanel();
    const fallback = screen
      .getAllByRole("rowheader")
      .find((h) => (h.textContent ?? "").includes("Neznámy model"));
    expect(fallback).toBeDefined();
    expect(fallback).toHaveTextContent("Neznámy model (záloha)");

    // Its two inputs are real, editable cells carrying the flat pair.
    expect(cell("vstup", "Neznámy model (záloha)")).toHaveValue(1.1);
    expect(cell("výstup", "Neznámy model (záloha)")).toHaveValue(5.5);

    // And it says WHEN it applies.
    expect(screen.getByText(/neuvádza model/)).toBeInTheDocument();
    expect(screen.getByText(/mimo troch rodín/)).toBeInTheDocument();
  });

  it("saves every edited cell under its OWN key (all 8 mappings)", async () => {
    const { onSave } = renderPanel();

    // A distinct new value per cell — a swapped family or in/out fails the assert.
    const edits: [string, "vstup" | "výstup", string, string][] = [
      ["Haiku", "vstup", "api_price_input_per_mtok_haiku", "1.11"],
      ["Haiku", "výstup", "api_price_output_per_mtok_haiku", "2.22"],
      ["Sonnet", "vstup", "api_price_input_per_mtok_sonnet", "3.33"],
      ["Sonnet", "výstup", "api_price_output_per_mtok_sonnet", "4.44"],
      ["Opus", "vstup", "api_price_input_per_mtok_opus", "5.55"],
      ["Opus", "výstup", "api_price_output_per_mtok_opus", "6.66"],
      ["Neznámy model (záloha)", "vstup", "api_price_input_per_mtok", "7.77"],
      ["Neznámy model (záloha)", "výstup", "api_price_output_per_mtok", "8.88"],
    ];

    for (const [model, direction, , value] of edits) {
      fireEvent.change(cell(direction, model), { target: { value } });
    }

    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(8));
    for (const [, , key, value] of edits) {
      expect(onSave).toHaveBeenCalledWith(key, value);
    }
  });

  it("sends only the edited cell — an untouched price is never rewritten", async () => {
    const { onSave } = renderPanel();

    fireEvent.change(cell("výstup", "Sonnet"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith("api_price_output_per_mtok_sonnet", "15");
  });

  it("disables every input and hides the save button when canEdit=false", () => {
    renderPanel({ canEdit: false });

    for (const model of ["Haiku", "Sonnet", "Opus", "Neznámy model (záloha)"]) {
      expect(cell("vstup", model)).toBeDisabled();
      expect(cell("výstup", model)).toBeDisabled();
    }
    expect(screen.queryByRole("button", { name: /Uložiť/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Iba na čítanie/)).toBeInTheDocument();
  });

  it("labels a 0 price as nenastavené, not as a price of zero", () => {
    renderPanel({
      settings: TOKEN_PRICE_KEYS.map((k) =>
        setting(k, k === "api_price_input_per_mtok_haiku" ? "0.0" : storedOf(k)),
      ),
    });
    // Exactly the one unset cell is flagged inside the table (the legend below it
    // explains the marker and is deliberately not counted here).
    const table = within(screen.getByRole("table"));
    expect(table.getAllByText("nenastavené")).toHaveLength(1);
    // …and the cell still shows the raw 0 rather than pretending to be blank.
    expect(cell("vstup", "Haiku")).toHaveValue(0);
    // The legend states what 0 means, so nobody reads it as "free".
    expect(screen.getByText(/náklad sa\s+nevyčísli/)).toBeInTheDocument();
  });

  it("warns when only one half of a family pair is set (backend falls back silently)", () => {
    renderPanel({
      settings: TOKEN_PRICE_KEYS.map((k) =>
        setting(k, k === "api_price_output_per_mtok_opus" ? "0.0" : storedOf(k)),
      ),
    });
    expect(screen.getByText(/Vyplnená len jedna z dvoch cien/)).toBeInTheDocument();
  });

  it("surfaces the provenance of a stored override as a tooltip", () => {
    renderPanel({
      settings: TOKEN_PRICE_KEYS.map((k) =>
        setting(k, storedOf(k), {
          is_default: k !== "api_price_input_per_mtok_opus",
          updated_by_username: k === "api_price_input_per_mtok_opus" ? "zoltan" : null,
          updated_at: k === "api_price_input_per_mtok_opus" ? "2026-07-20T10:00:00Z" : null,
        }),
      ),
    });
    expect(cell("vstup", "Opus")).toHaveAttribute("title", expect.stringContaining("Uložený override"));
    expect(cell("vstup", "Opus")).toHaveAttribute("title", expect.stringContaining("zoltan"));
    expect(cell("vstup", "Haiku")).toHaveAttribute("title", expect.stringContaining("Predvolená hodnota"));
  });

  it("keeps a failed save visible instead of flashing success", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("boom"));
    renderPanel({ onSave });

    fireEvent.change(cell("vstup", "Opus"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("✓ Uložené")).not.toBeInTheDocument();
    // The edit survives so the Manažér can retry.
    expect(cell("vstup", "Opus")).toHaveValue(20);
  });

  it("renders a load error instead of the table", () => {
    renderPanel({ loadError: "Nepodarilo sa načítať nastavenia." });
    expect(screen.getByText("Nepodarilo sa načítať nastavenia.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("exports exactly the eight keys the generic panel must filter out", () => {
    expect([...TOKEN_PRICE_KEYS].sort()).toEqual(
      [
        "api_price_input_per_mtok",
        "api_price_input_per_mtok_haiku",
        "api_price_input_per_mtok_opus",
        "api_price_input_per_mtok_sonnet",
        "api_price_output_per_mtok",
        "api_price_output_per_mtok_haiku",
        "api_price_output_per_mtok_opus",
        "api_price_output_per_mtok_sonnet",
      ].sort(),
    );
  });
});
