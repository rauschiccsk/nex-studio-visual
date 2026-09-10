/**
 * Nastavenia verzie — jediná cesta v produkte, ako opraviť hodnotu zadanú pri zakladaní (ICCINT-100).
 *
 * **Čo chýbalo.** Číslo verzie, Názov a Cieľový dátum sa dali zadať iba pri zakladaní. Potom už
 * nikde: stránka verzie ich len vypisovala a `updateVersion` volalo jediné miesto v celom rozhraní —
 * formulár novej verzie pri druhom pokuse o uloženie. Director si názov NEX Manager 1.1.0 zmenil,
 * zmena sa vtedy ticho zahodila (ICCINT-91), a keď ju chcel po dokončení stavby opraviť, **nemal
 * ako**: „Nemám možnosť (aspoň som nenašiel) ako premenovať verziu.“ Nenašiel preto, že tam nebola.
 *
 * Je to tá istá diera, ktorú pre PROJEKTY zavrel ICCINT-7. Hodnota, ktorú sa človek pomýli raz a
 * nesie ju navždy, je diera v samostatnosti kokpitu — bez zásahu do databázy sa opraviť nedala.
 *
 * ⚠️ **Číslo verzie nie je iba popiska.** Podľa neho sa volá priečinok s dokumentmi
 * (`docs/specs/versions/v<číslo>/`), takže sa smie meniť len dovtedy, kým podľa neho nič nevzniklo.
 * Keď sa už nesmie, pole je **zamknuté a povie prečo** — nie ticho nefunkčné, čo je presne tá chyba,
 * ktorú riešil ICCINT-91. Zámok vynucuje engine; toto je jeho zobrazenie, nie obrana.
 */

import { useEffect, useState } from "react";
import { Lock, Pencil } from "lucide-react";

import { getVersionSettings, updateVersion } from "@/services/api/versions";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import type { Version, VersionUpdate } from "@/types/version";

const INPUT_CLS =
  "w-full bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded-lg " +
  "px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500 " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const LABEL_CLS = "block text-xs font-medium text-[var(--color-text-muted)] mb-1";

interface Props {
  version: Version;
  canEdit: boolean;
  onSaved: (v: Version) => void;
}

interface FormState {
  version_number: string;
  name: string;
  target_date: string;
}

function toForm(v: Version): FormState {
  return {
    version_number: v.version_number,
    name: v.name ?? "",
    target_date: v.target_date ?? "",
  };
}

/** Iba to, čo sa naozaj zmenilo — prázdny objekt znamená, že sa engine nemá čím zaťažovať. */
function diff(v: Version, f: FormState): VersionUpdate {
  const out: VersionUpdate = {};
  if (f.version_number.trim() !== v.version_number) out.version_number = f.version_number.trim();
  if (f.name.trim() !== (v.name ?? "")) out.name = f.name.trim();
  if ((f.target_date || "") !== (v.target_date ?? "")) out.target_date = f.target_date;
  return out;
}

export default function VersionSettingsSection({ version, canEdit, onSaved }: Props) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<FormState>(() => toForm(version));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  // Veta, prečo sa číslo už nedá meniť. `null` = ešte sa dá; `undefined` = zatiaľ nevieme.
  const [lockReason, setLockReason] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    getVersionSettings(version.id)
      .then((s) => { if (!cancelled) setLockReason(s.version_number_lock_reason); })
      .catch(() => {
        // ⚠️ Keď sa to nedá zistiť, pole sa ZAMKNE — nie otvorí. Otvorené pole nad neznámym stavom
        // sľubuje zmenu, ktorú engine aj tak odmietne, a to je horšie než zamknuté pole s dôvodom.
        if (!cancelled) setLockReason("Nepodarilo sa zistiť, či sa číslo verzie ešte dá zmeniť.");
      });
    return () => { cancelled = true; };
  }, [version.id]);

  const numberLocked = lockReason !== null;

  const startEdit = () => {
    setForm(toForm(version));
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    const payload = diff(version, form);
    if (Object.keys(payload).length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await updateVersion(version.id, payload);
      onSaved(saved);
      setEditing(false);
    } catch (err) {
      setError(humanizeApiError(err, "Nastavenia verzie sa nepodarilo uložiť"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-6 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-widest">
          Nastavenia verzie
        </h2>
        {!editing && (
          <button
            type="button"
            onClick={startEdit}
            disabled={!canEdit}
            title={canEdit ? undefined : "Upravovať môže len vlastník projektu alebo správca."}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] text-xs font-medium px-3 py-1.5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Pencil className="w-3.5 h-3.5" />
            Upraviť
          </button>
        )}
      </div>

      <ErrorNote error={error} className="mb-4" />

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={LABEL_CLS} htmlFor="ver-number">
            Číslo verzie
          </label>
          <input
            id="ver-number"
            spellCheck={false}
            className={INPUT_CLS}
            value={editing ? form.version_number : version.version_number}
            disabled={!editing || saving || numberLocked}
            title={numberLocked && lockReason ? lockReason : undefined}
            onChange={(e) => setForm({ ...form, version_number: e.target.value })}
          />
          {/* Zamknuté pole musí povedať PREČO. Bez dôvodu je to len pole, ktoré nejde — a človek
              hľadá chybu u seba. */}
          {editing && numberLocked && lockReason && (
            <p className="mt-1 flex items-start gap-1 text-[11px] text-[var(--color-text-muted)]">
              <Lock className="w-3 h-3 mt-0.5 shrink-0" />
              <span>{lockReason}</span>
            </p>
          )}
        </div>

        <div>
          <label className={LABEL_CLS} htmlFor="ver-target-date">
            Cieľový dátum
          </label>
          <input
            id="ver-target-date"
            type="date"
            className={INPUT_CLS}
            value={editing ? form.target_date : (version.target_date ?? "")}
            disabled={!editing || saving}
            onChange={(e) => setForm({ ...form, target_date: e.target.value })}
          />
        </div>

        <div className="col-span-2">
          <label className={LABEL_CLS} htmlFor="ver-name">
            Názov
          </label>
          <input
            id="ver-name"
            lang="sk"
            spellCheck={true}
            className={INPUT_CLS}
            placeholder="napr. Inštalovateľná aplikácia PWA"
            value={editing ? form.name : (version.name ?? "")}
            disabled={!editing || saving}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
      </div>

      {editing && (
        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => { setEditing(false); setError(null); }}
            disabled={saving}
            className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50 transition-colors"
          >
            Zrušiť
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50 transition-colors"
          >
            {saving ? "Ukladám…" : "Uložiť"}
          </button>
        </div>
      )}
    </div>
  );
}
