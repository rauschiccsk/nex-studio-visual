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
import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Pencil } from "lucide-react";

import {
  commitAppKitApi,
  getAppKitStatusApi,
  getProjectCreatePresetsApi,
  updateProjectApi,
  upgradeAppKitApi,
  type AppKitStatus,
} from "@/services/api/projects";
import { AppLanguagesChoice } from "./AppLanguagesChoice";
import { kitNeeded, unknownToKit, type LanguageOption } from "@/lib/appLanguages";
import { PRIVATE_NETWORK_APPLIES, PRIVATE_NETWORK_HINT, PRIVATE_NETWORK_LABEL } from "./privateNetwork";
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
  private_network: boolean;
  languages: string[];
}

const SLOVAK_ONLY = ["sk"];

function languagesOf(p: ProjectRead): string[] {
  return p.languages?.length ? p.languages : SLOVAK_ONLY;
}

/** DEV-395: anything but Slovak alone — only then the kit has to know languages. */
function isMultilingual(languages: string[]): boolean {
  return languages.join(",") !== SLOVAK_ONLY.join(",");
}

/** „maďarčina (hlavný jazyk), slovenčina“ — as the backend says it in its sentences. */
function describe(languages: string[], options: LanguageOption[]): string {
  const names = languages.map((code) => options.find((o) => o.code === code)?.label ?? code);
  return names.length > 1 ? `${names[0]} (hlavný jazyk), ${names.slice(1).join(", ")}` : (names[0] ?? "");
}

/** „Jazyky slovenčina, maďarčina, angličtina“ — the languages without saying which one is the main. */
function describePlain(languages: string[], options: LanguageOption[]): string {
  return `Jazyky ${languages.map((code) => options.find((o) => o.code === code)?.label ?? code).join(", ")}`;
}

function toForm(p: ProjectRead): FormState {
  return {
    name: p.name,
    description: p.description ?? "",
    backend_port: p.backend_port?.toString() ?? "",
    frontend_port: p.frontend_port?.toString() ?? "",
    db_port: p.db_port?.toString() ?? "",
    guardian_enabled: p.guardian_enabled,
    private_network: p.private_network,
    languages: languagesOf(p),
  };
}

/** Only what actually changed — a PATCH that re-sends every field would make the port
 *  checks run on an edit that never touched a port. */
const PORT_FIELDS = ["backend_port", "frontend_port", "db_port"] as const;

/** DEV-324: an assigned port is never emptied — the server refuses it (backend/services/project.py, the projects
 *  table is the port ledger); the screen says so at the field before a save. */
const PORT_STAYS =
  "Pridelený port sa nedá vymazať — kokpit ho drží v evidencii portov, aby ho nedostal iný projekt, kým na ňom beží inštalácia. Zmeniť ho na iné číslo môžeš.";

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
  if (form.private_network !== project.private_network) out.private_network = form.private_network;
  if (form.languages.join(",") !== languagesOf(project).join(",")) out.languages = form.languages;
  return out;
}

export default function ProjectSettingsSection({ project, onSaved, canEdit }: Props) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<FormState>(() => toForm(project));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  // DEV-395: the languages an app on the template speaks — the options the cockpit offers, and whether the project's
  // nex-app-kit knows languages (an older one is upgraded here, before the languages are saved).
  const [languageOptions, setLanguageOptions] = useState<LanguageOption[]>([]);
  const [kit, setKit] = useState<AppKitStatus | null>(null);
  const [upgrading, setUpgrading] = useState(false);
  const onTemplate = Boolean(project.template);
  const wantsLanguages = editing && onTemplate && isMultilingual(form.languages);
  // DEV-400: the kit's status is read whenever the settings of a project on the template are edited — it also says
  // whether an upgrade stayed in the files unsaved.
  const readsKit = editing && onTemplate;

  useEffect(() => {
    if (!onTemplate) return;
    let cancelled = false;
    getProjectCreatePresetsApi()
      .then((p) => {
        if (!cancelled) setLanguageOptions(p.languages ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [onTemplate]);

  useEffect(() => {
    if (!readsKit || kit) return;
    let cancelled = false;
    getAppKitStatusApi(project.id)
      .then((status) => {
        if (!cancelled) setKit(status);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(humanizeApiError(err, "Verziu šablóny aplikácie sa nepodarilo zistiť"));
      });
    return () => {
      cancelled = true;
    };
  }, [readsKit, kit, project.id]);

  // DEV-399: each language comes with its own release of the kit (English later than Hungarian).
  const kitTooOld = wantsLanguages && kit !== null && unknownToKit(form.languages, kit.languages_known ?? []).length > 0;

  const upgradeKit = async () => {
    if (!kit?.latest) return;
    setUpgrading(true);
    setError(null);
    try {
      // DEV-400: an upgrade whose commit failed is in the status as `uncommitted` — the note below says it and offers
      // to save it; the result's `committed` is not left unread.
      await upgradeAppKitApi(project.id, kit.latest);
      setKit(await getAppKitStatusApi(project.id));
    } catch (err) {
      setError(humanizeApiError(err, "Šablónu aplikácie sa nepodarilo povýšiť"));
    } finally {
      setUpgrading(false);
    }
  };

  const saveKit = async () => {
    setUpgrading(true);
    setError(null);
    try {
      await commitAppKitApi(project.id);
      setKit(await getAppKitStatusApi(project.id));
    } catch (err) {
      setError(humanizeApiError(err, "Povýšenie šablóny sa nepodarilo uložiť do gitu"));
    } finally {
      setUpgrading(false);
    }
  };

  const startEdit = () => {
    setForm(toForm(project));
    setKit(null);
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

  const emptiedPorts = PORT_FIELDS.filter((f) => editing && form[f].trim() === "" && project[f] != null);

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
            data-no-draft="súčasť formulára nastavení projektu — ukladá sa spolu s ostatnými poliami, ktoré sa nepamätajú"
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

        {PORT_FIELDS.map((field, i) => (
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
            {emptiedPorts.includes(field) && (
              <p data-testid={`port-stays-${field}`} className="mt-1 text-xs text-[var(--color-state-error-fg)]">
                {PORT_STAYS}
              </p>
            )}
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

        <div className="col-span-2">
          <label className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={editing ? form.private_network : project.private_network}
              disabled={!editing || saving}
              onChange={(e) => setForm({ ...form, private_network: e.target.checked })}
            />
            <span>
              {PRIVATE_NETWORK_LABEL}
              <span className="block text-xs text-[var(--color-text-muted)]">
                {PRIVATE_NETWORK_HINT} {PRIVATE_NETWORK_APPLIES}
              </span>
            </span>
          </label>
        </div>

        {onTemplate && languageOptions.length > 0 && (
          <div className="col-span-2" data-testid="project-languages">
            <div className={LABEL_CLS}>Jazyky aplikácie</div>
            {editing ? (
              <AppLanguagesChoice
                options={languageOptions}
                value={form.languages}
                onChange={(languages) => setForm({ ...form, languages })}
                disabled={saving || upgrading}
              />
            ) : (
              <p className="text-sm text-[var(--color-text-primary)]">
                {describe(languagesOf(project), languageOptions)}
              </p>
            )}
            {editing && form.languages.join(",") !== languagesOf(project).join(",") && (
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                Zmena platí od najbližšieho ťahu agenta — preklady doplní agent v najbližšej verzii a kokpit ich pri
                Overení skontroluje.
              </p>
            )}
            {kitTooOld && kit && (
              <div
                data-testid="kit-too-old"
                className="mt-2 rounded-lg border border-[var(--color-status-warning)]/50 bg-[var(--color-status-warning)]/10 p-3 text-xs text-[var(--color-text-secondary)]"
              >
                <p>
                  {describePlain(form.languages, languageOptions)} pozná šablóna aplikácie od verzie nex-app-kit{" "}
                  {kitNeeded(form.languages, kit.languages_from ?? {})}, projekt stojí na{" "}
                  {kit.current ?? "verzii, ktorá sa nedá prečítať"}. Najprv šablónu povýš, potom jazyky ulož.
                </p>
                {kit.latest && (
                  <button
                    type="button"
                    onClick={() => void upgradeKit()}
                    disabled={upgrading || saving}
                    className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 font-medium text-[var(--color-text-primary)] disabled:opacity-50"
                  >
                    {upgrading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    Povýšiť šablónu na {kit.latest}
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {readsKit && kit?.uncommitted && (
          <div
            data-testid="kit-unsaved"
            className="col-span-2 rounded-lg border border-[var(--color-status-warning)]/50 bg-[var(--color-status-warning)]/10 p-3 text-xs text-[var(--color-text-secondary)]"
          >
            <p>
              Šablóna aplikácie je v súboroch projektu povýšená na {kit.uncommitted}, ale v gite projektu nie je uložená
              — agent by ju videl ako cudziu zmenu. Ulož ju.
            </p>
            <button
              type="button"
              onClick={() => void saveKit()}
              disabled={upgrading || saving}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 font-medium text-[var(--color-text-primary)] disabled:opacity-50"
            >
              {upgrading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Uložiť do gitu
            </button>
          </div>
        )}

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
            disabled={saving || upgrading || emptiedPorts.length > 0 || kitTooOld}
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
