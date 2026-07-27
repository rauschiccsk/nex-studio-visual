/**
 * TokenPricesPanel — the model price table in ⚙️ Nastavenia → Systém.
 *
 * The eight `api_price_*_per_mtok*` system settings used to render as eight
 * full-width setting cards (label + raw key + type + description + input +
 * provenance line) — several screens of scrolling to read three numbers the
 * Manažér checks most often. This collapses them into ONE compact table, one
 * row per model family we actually run:
 *
 *     Model                  | Cena vstup | Cena výstup
 *     Haiku / Sonnet / Opus  |     …      |      …
 *     Neznámy model (záloha) |     …      |      …
 *
 * Row order is the Director's: Haiku, Sonnet, Opus (cheapest → most capable).
 *
 * The fourth row is the FLAT pair (`api_price_{input,output}_per_mtok`) — the
 * fallback the backend applies when the usage envelope names no model, or names
 * one outside the three families (`_model_family` → `"_unknown"`, metrics.py).
 * It is rendered below a separator as visually secondary but NEVER hidden: an
 * invisible fallback price is exactly how a silent mis-pricing happens.
 *
 * This is a NEX-Studio-specific surface (the shared SettingsKit serves every ICC
 * app and knows nothing about Claude model families), so it lives here rather
 * than in nex-shared — same split as AutonomyDialPanel. It mirrors the shared
 * SystemSettingsPanel's IO contract exactly: the app threads the very same
 * `onSave(key, value)` through, drafts are seeded from the loaded rows, and the
 * "Uložený override — <user> · <date>" provenance is preserved (as a per-cell
 * tooltip + marker, so the table stays a table).
 *
 * Honesty rules encoded here (Náklady screen semantics, metrics.py):
 *   - 0 is NOT a price of zero — it is "nenastavené", and a phase priced by it
 *     renders "—" on Náklady. Such a cell is labelled as unset, never as free.
 *   - a per-family price applies only when BOTH halves of its pair are > 0
 *     (`_resolve_price`); a half-filled row silently falls back to the flat
 *     pair, so it is called out on the row.
 */

import { useEffect, useState } from "react";

import { humanizeApiError } from "@/services/apiError";
import type { SystemSettingRead } from "@/types/system_setting";
import {
  FALLBACK_PRICE_ROW,
  MODEL_PRICE_ROWS,
  TOKEN_PRICE_KEYS,
  type PriceRow,
} from "@/components/settings/tokenPrices";

export interface TokenPricesPanelProps {
  /** The loaded system settings — only the eight price keys are read. */
  settings: SystemSettingRead[];
  /** Whether the current user may edit (ri role); else the inputs are disabled. */
  canEdit: boolean;
  /** Persist one setting. Same signature the shared SystemSettingsPanel uses. */
  onSave: (key: string, value: string) => Promise<SystemSettingRead>;
  /** Initial load in flight. */
  loading?: boolean;
  /** Load error (empty = none). */
  loadError?: string;
}

/** `true` when the stored/typed value carries no usable price.
 *
 * The threshold mirrors the backend gate exactly (`metrics.py` `_resolve_price`: a family price
 * applies only when BOTH halves are `> 0`). A NEGATIVE value stores fine — nothing validates the
 * sign — and would otherwise render as a perfectly normal price here while metrics silently costed
 * that model at the fallback pair. Anything not strictly positive is "not configured". */
function isUnset(raw: string): boolean {
  const n = Number.parseFloat(raw);
  return !Number.isFinite(n) || n <= 0;
}

/** The kit's provenance sentence, as a tooltip string. */
function provenanceOf(setting: SystemSettingRead | undefined): string {
  if (!setting) return "Nastavenie sa nenačítalo.";
  if (setting.is_default) return "Predvolená hodnota — zatiaľ neuložená žiadna vlastná cena.";
  const who = setting.updated_by_username ? ` — ${setting.updated_by_username}` : "";
  const when = setting.updated_at ? ` · ${new Date(setting.updated_at).toLocaleString("sk-SK")}` : "";
  return `Uložený override${who}${when}`;
}

export function TokenPricesPanel({
  settings,
  canEdit,
  onSave,
  loading = false,
  loadError = "",
}: TokenPricesPanelProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const [flash, setFlash] = useState(false);

  // Seed a draft per key on first sight of the row, exactly like the shared
  // panel does — never clobbering an edit already in progress.
  useEffect(() => {
    setDrafts((prev) => {
      const next = { ...prev };
      for (const s of settings) if (!(s.key in next)) next[s.key] = s.value;
      return next;
    });
  }, [settings]);

  const byKey = new Map(settings.map((s) => [s.key, s]));

  const draftOf = (key: string) => drafts[key] ?? byKey.get(key)?.value ?? "";
  const isDirty = (key: string) => {
    const stored = byKey.get(key);
    return stored !== undefined && draftOf(key) !== stored.value;
  };

  const dirtyKeys = TOKEN_PRICE_KEYS.filter(isDirty);

  async function handleSave() {
    if (dirtyKeys.length === 0) return;
    setSaving(true);
    setSaveErrors({});
    const errors: Record<string, string> = {};
    for (const key of dirtyKeys) {
      // A cleared cell (or a Slovak decimal comma, which a number input normalises to "") would be
      // PATCHed as "" and rejected by the backend (`value: str = Field(..., min_length=1)`) — while
      // the cell shows "nenastavené", reading as if the clear had been accepted. The shared kit
      // guards the same way. To unset a price, type 0.
      if (draftOf(key).trim() === "") {
        errors[key] = "Prázdne pole sa neuloží — pre nenastavenú cenu zadaj 0.";
        continue;
      }
      try {
        const updated = await onSave(key, draftOf(key));
        setDrafts((prev) => ({ ...prev, [key]: updated.value }));
      } catch (e: unknown) {
        errors[key] = humanizeApiError(e, "Uloženie zlyhalo").message;
      }
    }
    setSaveErrors(errors);
    setSaving(false);
    if (Object.keys(errors).length === 0) {
      setFlash(true);
      setTimeout(() => setFlash(false), 2000);
    }
  }

  /** One editable price cell. */
  function priceCell(row: PriceRow, key: string, direction: "vstup" | "výstup") {
    const setting = byKey.get(key);
    const value = draftOf(key);
    const unset = isUnset(value);
    const err = saveErrors[key];
    const overridden = Boolean(setting && !setting.is_default);
    return (
      <td className="px-3 py-2 align-top">
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            step="any"
            min="0"
            value={value}
            aria-label={`Cena ${direction} — ${row.label}`}
            title={provenanceOf(setting)}
            disabled={!canEdit || !setting}
            onChange={(e) => setDrafts((prev) => ({ ...prev, [key]: e.target.value }))}
            className={`w-28 bg-[var(--color-surface)] border rounded px-2 py-1 text-xs text-[var(--color-text-primary)] font-mono text-right focus:outline-none focus:border-primary-500 disabled:opacity-50 disabled:cursor-not-allowed ${
              isDirty(key)
                ? "border-primary-500"
                : "border-[var(--color-border-default)]"
            }`}
          />
          {/* Provenance marker — the tooltip carries the full "Uložený override
              — <user> · <date>" the shared panel prints as a whole line. */}
          <span
            title={provenanceOf(setting)}
            aria-hidden="true"
            className={`text-[10px] leading-none ${
              overridden
                ? "text-[var(--color-text-muted)]"
                : "text-transparent"
            }`}
          >
            ●
          </span>
        </div>
        {/* 0 is "nenastavené", never a real price of zero (Náklady renders "—"). */}
        {unset && (
          <div className="mt-0.5 text-[10px] text-[var(--color-status-warning)]">nenastavené</div>
        )}
        {err && (
          <div className="mt-0.5 text-[10px] text-[var(--color-status-error)]">{err}</div>
        )}
      </td>
    );
  }

  function priceRow(row: PriceRow, secondary: boolean) {
    const inUnset = isUnset(draftOf(row.inputKey));
    const outUnset = isUnset(draftOf(row.outputKey));
    // metrics.py `_resolve_price`: a family price applies only when BOTH halves
    // are > 0 — half-filled silently falls back to the flat pair below.
    const halfSet = !secondary && inUnset !== outUnset;
    return (
      <tr key={row.id} className={secondary ? "" : "border-t border-[var(--color-border-subtle)]"}>
        <th
          scope="row"
          className={`px-3 py-2 text-left align-top font-medium ${
            secondary
              ? "text-xs text-[var(--color-text-muted)]"
              : "text-sm text-[var(--color-text-primary)]"
          }`}
        >
          {row.label}
          {halfSet && (
            <div className="mt-0.5 max-w-[16rem] text-[10px] font-normal text-[var(--color-status-warning)] leading-snug">
              Vyplnená len jedna z dvoch cien — použije sa záložná cena nižšie. Vyplň obe.
            </div>
          )}
        </th>
        {priceCell(row, row.inputKey, "vstup")}
        {priceCell(row, row.outputKey, "výstup")}
      </tr>
    );
  }

  return (
    <div className="p-6 max-w-3xl">
      <h2 className="text-sm font-semibold text-[var(--color-text-secondary)] mb-1">
        Ceny modelov
      </h2>
      <p className="text-xs text-[var(--color-text-muted)] mb-4 leading-relaxed">
        Cena Claude API v <span className="font-medium">eurách za milión tokenov</span>, zvlášť za vstup
        (to, čo agentovi pošleme) a za výstup (to, čo napíše). Podľa nich sa počítajú sumy na obrazovke{" "}
        <span className="font-medium">Náklady</span>.{" "}
        {canEdit
          ? "Zmeny sa prejavia do 30 s."
          : "Iba na čítanie — na úpravu chýba oprávnenie."}
      </p>

      {loadError && (
        <div className="rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-3 py-2 text-xs text-[var(--color-state-error-fg)] mb-4">
          {loadError}
        </div>
      )}
      {loading && !loadError && (
        <div className="text-xs text-[var(--color-text-muted)]">Načítavam…</div>
      )}

      {!loading && !loadError && (
        <>
          <div className="overflow-x-auto rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)]">
            <table className="w-full border-collapse">
              <caption className="sr-only">Ceny modelov v eurách za milión tokenov</caption>
              <thead>
                <tr className="bg-[var(--color-surface-hover)]">
                  <th
                    scope="col"
                    className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text-secondary)]"
                  >
                    Model
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text-secondary)]"
                  >
                    Cena vstup <span className="font-normal normal-case tracking-normal">(€ / mil. tokenov)</span>
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text-secondary)]"
                  >
                    Cena výstup <span className="font-normal normal-case tracking-normal">(€ / mil. tokenov)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {MODEL_PRICE_ROWS.map((r) => priceRow(r, false))}
              </tbody>
              {/* The fallback pair — separated + secondary, but never hidden. */}
              <tbody className="border-t-2 border-[var(--color-border-strong)]">
                {priceRow(FALLBACK_PRICE_ROW, true)}
                <tr>
                  <td colSpan={3} className="px-3 pb-2 text-[10px] text-[var(--color-text-muted)] leading-relaxed">
                    Použije sa, keď spotreba neuvádza model, alebo uvádza model mimo troch rodín vyššie —
                    aj vtedy, keď má niektorá rodina vyplnenú len jednu z dvoch cien.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Save row — one action for the whole table (only dirty cells are sent). */}
          <div className="mt-3 flex items-center gap-3 flex-wrap">
            {canEdit && (
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || dirtyKeys.length === 0}
                className="px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed rounded transition-colors"
              >
                {saving ? "Ukladám…" : dirtyKeys.length > 0 ? "Uložiť" : "Uložené"}
              </button>
            )}
            {flash && <span className="text-[11px] text-[var(--color-status-success)]">✓ Uložené</span>}
            <span className="text-[11px] text-[var(--color-text-muted)]">
              <span className="text-[var(--color-status-warning)]">nenastavené</span> = 0 → náklad sa
              nevyčísli, na obrazovke Náklady sa zobrazí „—". Nie je to cena nula.
            </span>
          </div>
        </>
      )}
    </div>
  );
}
