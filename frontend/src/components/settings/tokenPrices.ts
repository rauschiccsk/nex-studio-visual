/**
 * The model price key map behind TokenPricesPanel.
 *
 * Kept in its own module (not in the component file) because SettingsPage needs
 * `TOKEN_PRICE_KEYS` to filter these rows OUT of the generic SystemSettingsPanel,
 * and a component file that also exports constants breaks React Fast Refresh.
 *
 * The `_haiku` / `_sonnet` / `_opus` suffixes MUST stay in lockstep with the
 * backend `_PRICE_FAMILIES` (backend/services/metrics.py) — the suffix is what
 * `_resolve_price` looks up for a model's family.
 */

/** One model family's IN/OUT price pair. */
export interface PriceRow {
  id: string;
  /** Slovak row label. */
  label: string;
  /** System-setting key holding the input (prompt) price. */
  inputKey: string;
  /** System-setting key holding the output (completion) price. */
  outputKey: string;
}

/** The three families we run, in the Director's order (Haiku, Sonnet, Opus). */
export const MODEL_PRICE_ROWS: PriceRow[] = [
  {
    id: "haiku",
    label: "Haiku",
    inputKey: "api_price_input_per_mtok_haiku",
    outputKey: "api_price_output_per_mtok_haiku",
  },
  {
    id: "sonnet",
    label: "Sonnet",
    inputKey: "api_price_input_per_mtok_sonnet",
    outputKey: "api_price_output_per_mtok_sonnet",
  },
  {
    id: "opus",
    label: "Opus",
    inputKey: "api_price_input_per_mtok_opus",
    outputKey: "api_price_output_per_mtok_opus",
  },
];

/**
 * The flat fallback pair — used for the `_unknown` family (the envelope named no
 * model, or named one outside the three families) and for any family whose pair
 * is not fully filled in.
 */
export const FALLBACK_PRICE_ROW: PriceRow = {
  id: "flat",
  label: "Neznámy model (záloha)",
  inputKey: "api_price_input_per_mtok",
  outputKey: "api_price_output_per_mtok",
};

/**
 * Every key TokenPricesPanel owns. SettingsPage filters these out of the generic
 * SystemSettingsPanel's list so they never double-render (same precedent as
 * MIERA_AUTONOMIE_KEY).
 */
export const TOKEN_PRICE_KEYS: string[] = [
  ...MODEL_PRICE_ROWS.flatMap((r) => [r.inputKey, r.outputKey]),
  FALLBACK_PRICE_ROW.inputKey,
  FALLBACK_PRICE_ROW.outputKey,
];
