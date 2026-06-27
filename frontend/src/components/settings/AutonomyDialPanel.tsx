/**
 * AutonomyDialPanel — the Miera autonómie dial surface in ⚙️ Nastavenia
 * (NEX Studio v2.0.0, CR-V2-030 / SET-1).
 *
 * Renders the 4-preset Miera autonómie dial (design §2.3 / §4.6) as the GLOBAL
 * default — how often the AI Agent stops at a *schvaľovací bod* for the Manažér's
 * approval. The value is the `miera_autonomie` system setting (string KV); this
 * is a Studio-specific widget (the v2 engine's autonomy concept), so it lives in
 * the Studio Settings page rather than the shared SettingsKit, which serves
 * multiple ICC apps.
 *
 * The four presets + their order MUST stay in lockstep with the backend's
 * canonical `MIERA_AUTONOMIE_VALUES` (orchestrator.py) — declaration order is
 * ascending human-oversight (fewest → most stops). The two stops that are ALWAYS
 * outside the dial (the Špecifikácia approval at the end of Príprava + deploy
 * UAT/PROD) are documented here so the Manažér sees them, never controlled by the
 * picker (backend `ALWAYS_STOP_BOUNDARIES`).
 *
 * Reads/writes the `miera_autonomie` row via the standard system-settings IO the
 * app injects (same canEdit/ri gating as SystemSettingsPanel). The per-project /
 * per-build override is surfaced as documentation (the resolver layers them at
 * dispatch — design §2.3 AUTON-6); this panel sets only the global default.
 */

import { useState } from "react";

/** The dial preset machine value — mirrors the backend `MIERA_AUTONOMIE_VALUES`. */
export type MieraAutonomieLevel =
  | "plna"
  | "len_na_konci"
  | "pri_klucovych_bodoch"
  | "po_kazdej_faze";

interface DialPreset {
  id: MieraAutonomieLevel;
  label: string;
  /** Where in the build it stops (the *schvaľovacie body* it halts at). */
  stops: string;
  /** One-line intent. */
  detail: string;
}

// Order = ascending human-oversight (least → most stops), matching the backend
// canonical tuple. The descriptions mirror design §2.3 + the DEFAULT_SETTINGS
// docstring so the UI never drifts from the engine's behaviour.
const DIAL_PRESETS: DialPreset[] = [
  {
    id: "plna",
    label: "Plná autonómia",
    stops: "Žiadne medzizastávky",
    detail: "AI Agent prejde celý build (Návrh → Programovanie → Verifikácia) bez zastavenia.",
  },
  {
    id: "len_na_konci",
    label: "Len na konci",
    stops: "Po Verifikácii",
    detail: "Zastaví sa až keď je build overený/hotový — jedno schválenie na konci.",
  },
  {
    id: "pri_klucovych_bodoch",
    label: "Pri kľúčových bodoch",
    stops: "Po Návrhu + po Verifikácii",
    detail: "Zastaví sa po Návrhu (návrh + plán úloh) a po Verifikácii.",
  },
  {
    id: "po_kazdej_faze",
    label: "Po každej fáze",
    stops: "Po Návrhu + Programovaní + Verifikácii",
    detail: "Zastaví sa po každej riadenej fáze — maximálna kontrola.",
  },
];

export interface AutonomyDialPanelProps {
  /** The current global value (the `miera_autonomie` setting's value). */
  value: string;
  /** Whether this row is a service-layer default (no stored override yet). */
  isDefault: boolean;
  /** Username + timestamp of the last edit (null for a default). */
  updatedByUsername: string | null;
  updatedAt: string | null;
  /** Whether the current user may edit (ri role); else read-only. */
  canEdit: boolean;
  /** Persist the chosen level. Resolves on success, rejects with an Error. */
  onSave: (level: MieraAutonomieLevel) => Promise<void>;
  /** Initial load in flight. */
  loading?: boolean;
  /** Load error (empty = none). */
  loadError?: string;
}

function normalizeLevel(value: string): MieraAutonomieLevel {
  const v = value.trim();
  return DIAL_PRESETS.some((p) => p.id === v) ? (v as MieraAutonomieLevel) : "plna";
}

export function AutonomyDialPanel({
  value,
  isDefault,
  updatedByUsername,
  updatedAt,
  canEdit,
  onSave,
  loading = false,
  loadError = "",
}: AutonomyDialPanelProps) {
  const current = normalizeLevel(value);
  const [draft, setDraft] = useState<MieraAutonomieLevel>(current);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [flash, setFlash] = useState(false);

  // Keep the draft in sync if the canonical value changes underneath (e.g. a
  // fresh load resolves after first render) — but never clobber an in-flight
  // edit the user has already moved off the saved value.
  const dirty = draft !== current;

  async function handleSave() {
    if (!dirty) return;
    setSaving(true);
    setSaveError("");
    try {
      await onSave(draft);
      setFlash(true);
      setTimeout(() => setFlash(false), 2000);
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Neznáma chyba.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-6 max-w-3xl">
      <h2 className="text-sm font-semibold text-[var(--color-text-secondary)] mb-1">
        Miera autonómie
      </h2>
      <p className="text-xs text-[var(--color-text-muted)] mb-4 leading-relaxed">
        Ako často sa AI Agent zastaví na <span className="font-medium">schvaľovacom bode</span> pre
        Manažéra. Toto je <span className="font-medium">globálna predvolená</span> úroveň —
        prepísateľná per projekt aj per build (pri spustení); pri builde sa použije prvá nastavená
        vrstva (build → projekt → globál). Dial zároveň škáluje hĺbku Audítora.
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
          {/* The 4 presets — radio-card group (one global default). */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" role="radiogroup" aria-label="Miera autonómie">
            {DIAL_PRESETS.map((p) => {
              const selected = draft === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={!canEdit}
                  onClick={() => canEdit && setDraft(p.id)}
                  className={`p-3 rounded-lg border text-left transition-colors disabled:cursor-not-allowed ${
                    selected
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-[var(--color-border-default)] bg-[var(--color-canvas)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-strong)]"
                  } ${!canEdit && !selected ? "opacity-60" : ""}`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-sm font-semibold">{p.label}</span>
                    {selected && <span className="text-[10px] uppercase tracking-widest">Vybraté</span>}
                  </div>
                  <div className="text-[11px] text-[var(--color-text-muted)] mb-1">
                    Zastávky: <span className="text-[var(--color-text-secondary)]">{p.stops}</span>
                  </div>
                  <p className="text-[11px] text-[var(--color-text-muted)] leading-relaxed">{p.detail}</p>
                </button>
              );
            })}
          </div>

          {/* Save row + provenance. */}
          <div className="mt-3 flex items-center gap-3 flex-wrap">
            {canEdit && (
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !dirty}
                className="px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed rounded transition-colors"
              >
                {saving ? "Ukladám…" : dirty ? "Uložiť" : "Uložené"}
              </button>
            )}
            <span className="text-[11px] text-[var(--color-text-muted)]">
              {isDefault ? (
                "Predvolená hodnota."
              ) : (
                <>
                  Uložený override
                  {updatedByUsername && (
                    <>
                      {" "}
                      — <span className="text-[var(--color-text-secondary)] font-medium">{updatedByUsername}</span>
                    </>
                  )}
                  {updatedAt && <> · {new Date(updatedAt).toLocaleString("sk-SK")}</>}
                </>
              )}
            </span>
            {flash && <span className="text-[11px] text-[var(--color-status-success)]">✓ Uložené</span>}
            {saveError && <span className="text-[11px] text-[var(--color-status-error)]">{saveError}</span>}
          </div>

          {/* The two stops ALWAYS outside the dial (design §2.3, D3/D6). */}
          <div className="mt-5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface-hover)] p-3">
            <h3 className="text-[11px] font-semibold text-[var(--color-text-secondary)] uppercase tracking-widest mb-2">
              Vždy mimo dialu
            </h3>
            <ul className="space-y-1.5 text-[11px] text-[var(--color-text-muted)] leading-relaxed">
              <li>
                <span className="text-[var(--color-text-secondary)] font-medium">Schválenie špecifikácie</span>{" "}
                (koniec Prípravy) je <span className="font-medium">vždy</span> povinná zastávka — bez ohľadu na
                úroveň dialu.
              </li>
              <li>
                <span className="text-[var(--color-text-secondary)] font-medium">Nasadenie (UAT / PROD)</span> je{" "}
                <span className="font-medium">vždy</span> samostatná, manuálna, per-zákazník akcia — mimo dialu.
              </li>
            </ul>
          </div>

          {!canEdit && (
            <p className="mt-4 text-[11px] text-[var(--color-text-muted)] italic">
              Read-only — na úpravu chýba oprávnenie.
            </p>
          )}
        </>
      )}
    </div>
  );
}
