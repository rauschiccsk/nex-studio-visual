/**
 * Nastavenia projektu — the only place a founded project can be corrected (ICCINT-7).
 *
 * The backend has always accepted ``PATCH /projects/{id}``; the cockpit never called it.
 * A value typed at creation was therefore final, and on 21.08.2026 a project recorded on
 * another system's reserved port block had to be repaired directly in the database,
 * because the product offered no other way.
 *
 * Read first, edit deliberately. Deliberately the OPPOSITE of the product card in
 * nex-productcatalogs, which is directly editable: that one is touched daily by operators,
 * this one perhaps twice a year — and a port typed here has consequences on disk. One
 * extra click is a guard, not an obstacle.
 *
 * What is NOT here, and why (approved 21.08.2026):
 *   - repository URL — changes once in a lifetime, and more risk than use;
 *   - source path / KB path — they look like fields but moving files is not what setting
 *     them does. The project would simply point at a directory that is not there;
 *   - status — a lifecycle act belongs among actions, not in a form where it can be
 *     flipped by accident.
 *
 * Slug, project type and login mode are shown DISABLED with the reason rather than
 * hidden: whoever looks for them should see that they exist and why they are fixed.
 */
import { useState } from "react";
import { AlertTriangle, Loader2, Pencil } from "lucide-react";

import { updateProjectApi } from "@/services/api/projects";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import type { ProjectRead, ProjectUpdate } from "@/types";

const INPUT_CLS =
  "w-full bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded-lg " +
  "px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500 " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const LABEL_CLS = "block text-xs font-medium text-[var(--color-text-muted)] mb-1";

interface Props {
  project: ProjectRead;
  /** Called with the saved project so the page can re-render without a refetch. */
  onSaved: (project: ProjectRead) => void;
  /** False for a user who may look but not change this project. */
  canEdit: boolean;
}

interface FormState {
  name: string;
  description: string;
  backend_port: string;
  frontend_port: string;
  db_port: string;
  guardian_enabled: boolean;
}

function toForm(p: ProjectRead): FormState {
  return {
    name: p.name,
    description: p.description ?? "",
    backend_port: p.backend_port?.toString() ?? "",
    frontend_port: p.frontend_port?.toString() ?? "",
    db_port: p.db_port?.toString() ?? "",
    guardian_enabled: p.guardian_enabled,
  };
}

/** Only what actually changed — a PATCH that re-sends every field would make the port
 *  checks run on an edit that never touched a port. */
function diff(project: ProjectRead, form: FormState): ProjectUpdate {
  const out: ProjectUpdate = {};
  const port = (raw: string): number | null => (raw.trim() === "" ? null : Number(raw));

  if (form.name !== project.name) out.name = form.name.trim();
  if (form.description !== (project.description ?? "")) out.description = form.description;
  if (port(form.backend_port) !== project.backend_port) out.backend_port = port(form.backend_port);
  if (port(form.frontend_port) !== project.frontend_port)
    out.frontend_port = port(form.frontend_port);
  if (port(form.db_port) !== project.db_port) out.db_port = port(form.db_port);
  if (form.guardian_enabled !== project.guardian_enabled)
    out.guardian_enabled = form.guardian_enabled;
  return out;
}

export default function ProjectSettingsSection({ project, onSaved, canEdit }: Props) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<FormState>(() => toForm(project));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const startEdit = () => {
    setForm(toForm(project));
    setError(null);
    setWarnings([]);
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setError(null);
  };

  const save = async () => {
    const payload = diff(project, form);
    if (Object.keys(payload).length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await updateProjectApi(project.id, payload);
      // The registry write-back is best-effort; when it fails the project IS saved but the
      // block is unrecorded, and the next project could be handed it. Say so.
      setWarnings(saved.setup_warnings ?? []);
      onSaved(saved);
      setEditing(false);
    } catch (err) {
      setError(humanizeApiError(err, "Nastavenia projektu sa nepodarilo uložiť"));
    } finally {
      setSaving(false);
    }
  };

  const touchesPorts =
    form.backend_port !== (project.backend_port?.toString() ?? "") ||
    form.frontend_port !== (project.frontend_port?.toString() ?? "") ||
    form.db_port !== (project.db_port?.toString() ?? "");

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-widest">
          Nastavenia projektu
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

      {warnings.length > 0 && (
        <div className="mb-4 rounded-lg border border-[var(--color-status-warning)]/50 bg-[var(--color-status-warning)]/10 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[var(--color-status-warning)] mt-0.5 shrink-0" />
            <div className="text-xs text-[var(--color-text-secondary)] space-y-1">
              {warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          </div>
        </div>
      )}

      {error && <ErrorNote error={error} className="mb-4" />}

      <div className="grid grid-cols-2 gap-4 text-sm">
        <div className="col-span-2">
          <label className={LABEL_CLS} htmlFor="proj-name">
            Názov
          </label>
          <input
            spellCheck={false}
            id="proj-name"
            className={INPUT_CLS}
            value={editing ? form.name : project.name}
            disabled={!editing || saving}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          {editing && form.name !== project.name && (
            <p className="text-[11px] text-[var(--color-text-muted)] mt-1">
              Premenovanie zmení len názov. Priečinok na disku aj repozitár si nechajú pôvodný
              krátky názov <span className="font-mono">{project.slug}</span>.
            </p>
          )}
        </div>

        <div className="col-span-2">
          <label className={LABEL_CLS} htmlFor="proj-desc">
            Popis
          </label>
          <textarea
            lang="sk"
            spellCheck={true}
            id="proj-desc"
            rows={2}
            className={INPUT_CLS}
            value={editing ? form.description : (project.description ?? "")}
            disabled={!editing || saving}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </div>

        {(["backend_port", "frontend_port", "db_port"] as const).map((field, i) => (
          <div key={field} className={i === 2 ? "col-span-2 sm:col-span-1" : ""}>
            <label className={LABEL_CLS} htmlFor={`proj-${field}`}>
              {["Backend", "Frontend", "Databáza"][i]}
            </label>
            <input
              spellCheck={false}
              id={`proj-${field}`}
              inputMode="numeric"
              className={`${INPUT_CLS} font-mono`}
              value={editing ? form[field] : (project[field]?.toString() ?? "—")}
              disabled={!editing || saving}
              onChange={(e) => setForm({ ...form, [field]: e.target.value })}
            />
          </div>
        ))}

        {editing && touchesPorts && (
          <div className="col-span-2 rounded-lg border border-[var(--color-status-warning)]/50 bg-[var(--color-status-warning)]/10 p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-[var(--color-status-warning)] mt-0.5 shrink-0" />
              <div className="text-xs text-[var(--color-text-secondary)]">
                <p className="font-medium text-[var(--color-text-primary)]">
                  Zmena portu neprenasadí nič.
                </p>
                <p className="mt-1">
                  Prepíše sa evidencia — tu aj v znalostnej báze — ale bežiace kontajnery si
                  podržia staré porty. Aplikácia beží podľa svojho súboru{" "}
                  <span className="font-mono">docker-compose.yml</span>, do ktorého kokpit
                  nesiaha. Skutočný presun sú dva kroky: prepísať port tam a nasadiť nanovo.
                </p>
              </div>
            </div>
          </div>
        )}

        <div className="col-span-2">
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              checked={editing ? form.guardian_enabled : project.guardian_enabled}
              disabled={!editing || saving}
              onChange={(e) => setForm({ ...form, guardian_enabled: e.target.checked })}
            />
            Guardian
          </label>
        </div>

        {/* Disabled, never hidden — whoever looks for these should see that they exist and
            why they cannot change. Paths on disk and the repository hang off the slug; the
            structure generated at creation hangs off type and login mode. */}
        <div className="col-span-2 pt-2 border-t border-[var(--color-border-default)]">
          <p className="text-[11px] text-[var(--color-text-muted)] mb-2">
            Nasledujúce sa po založení nemenia — visia na nich cesty na disku, repozitár a
            štruktúra, ktorá pri zakladaní vznikla.
          </p>
          <div className="grid grid-cols-3 gap-3">
            {[
              ["proj-slug", "Krátky názov", project.slug],
              ["proj-type", "Druh projektu", project.type],
              ["proj-auth", "Prihlásenie", project.auth_mode],
            ].map(([id, label, value]) => (
              <div key={id}>
                <label className={LABEL_CLS} htmlFor={id}>
                  {label}
                </label>
                <input
                  spellCheck={false}
                  id={id}
                  className={`${INPUT_CLS} font-mono`}
                  value={value}
                  disabled
                  readOnly
                />
              </div>
            ))}
          </div>
        </div>
      </div>

      {editing && (
        <div className="flex items-center gap-2 mt-4">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium px-4 py-2 transition-colors disabled:opacity-50"
          >
            {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Uložiť
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={saving}
            className="rounded-lg border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] text-xs font-medium px-4 py-2 transition-colors disabled:opacity-50"
          >
            Zrušiť
          </button>
        </div>
      )}
    </div>
  );
}
