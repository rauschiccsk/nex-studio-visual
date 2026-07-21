import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  listProjectsApi,
  getNexsharedStatusApi,
  upgradeNexsharedApi,
  getGitStatusApi,
  commitGitApi,
  discardGitApi,
  type NexsharedStatus,
  type GitStatus,
} from "@/services/api/projects";
import { NexsharedUpgradePrompt } from "@/components/riadiace/NexsharedUpgradePrompt";
import { DirtyTreeGuard } from "@/components/riadiace/DirtyTreeGuard";
import { listVersions, createVersion, writeZadanie } from "@/services/api/versions";
import { postPipelineActionApi } from "@/services/api/pipeline";
import { useActiveContextStore } from "@/store/activeContextStore";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ─── Helpers ──────────────────────────────────────────────────────────────────

// Suggest the next version number. Stored WITHOUT a leading "v": the canonical format is bare
// semver (the post-scaffold seeds the first version as "0.1.0"), and the orchestrator's spec-tree
// path helper prepends the "v" itself (docs/specs/versions/v<version_number>/…) — so a stored "v"
// would double it. The first version produced by the v2 pipeline is v0.1.0 (design §4.3 / DEPLOY-9).
function nextVersionNumber(versions: Version[]): string {
  if (versions.length === 0) return "0.1.0";
  const last = [...versions].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )[0];
  if (!last) return "0.1.0";
  // Accept an optional leading "v" + an optional patch segment (semver or the older major.minor).
  const m = last.version_number.match(/^v?(\d+)\.(\d+)(?:\.(\d+))?$/);
  if (!m || !m[1] || !m[2]) return "";
  const maj = parseInt(m[1]);
  const min = parseInt(m[2]);
  const nextMin = min + 1;
  return nextMin >= 10 ? `${maj + 1}.0.0` : `${maj}.${nextMin}.0`;
}

// ─── Input style ──────────────────────────────────────────────────────────────

const inputCls =
  "w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary-500 transition-colors";

// ─── NewVersionPage ───────────────────────────────────────────────────────────

export default function NewVersionPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [prevVersions, setPrevVersions] = useState<Version[]>([]);
  const [loadError, setLoadError] = useState("");

  const [versionNumber, setVersionNumber] = useState("");
  const [versionManual, setVersionManual] = useState(false);
  const [name, setName] = useState("");
  const [targetDate, setTargetDate] = useState("");
  // The Zadanie — the free-text brief, the MAIN input (design §4.3). Persisted on save to
  // docs/specs/versions/v<N>/customer-requirements.md; the Príprava phase reads it.
  const [zadanie, setZadanie] = useState("");

  // Two-step flow (design §4.3, "no autopilot"): (1) "Uložiť Zadanie" creates the version row +
  // writes customer-requirements.md → reveals (2) "Spustiť tvorbu špecifikácie" which begins the
  // Príprava phase. ``savedVersion`` is the created row once step 1 succeeds.
  const [savedVersion, setSavedVersion] = useState<Version | null>(null);

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<HumanError | null>(null);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);

  // #3 auto-notify: is the app behind the latest nex-shared? (opt-in upgrade before the build)
  const [nexStatus, setNexStatus] = useState<NexsharedStatus | null>(null);
  const [nexBusy, setNexBusy] = useState(false);
  const [nexDismissed, setNexDismissed] = useState(false);

  // v4.0.25 dirty-tree guard: block founding until the project's working tree is clean, so the
  // pipeline never starts from an uncommitted baseline it can't scope (Tibor/Nazar lens).
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const [gitBusy, setGitBusy] = useState(false);

  const verRef = useRef<HTMLInputElement>(null);

  // Load project + existing versions
  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    listProjectsApi({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        const found = res.items.find((p) => p.slug === slug);
        if (!found) { setLoadError("Projekt nebol nájdený."); return; }
        setProject(found);
        // #3: check nex-shared freshness (best-effort — a failure just skips the prompt).
        getNexsharedStatusApi(found.id)
          .then((st) => { if (!cancelled) setNexStatus(st); })
          .catch(() => {});
        // v4.0.25: preflight the working tree — a dirty tree blocks founding until resolved.
        getGitStatusApi(found.id)
          .then((gs) => { if (!cancelled) setGitStatus(gs); })
          .catch(() => {});
        return listVersions(found.id).then((vs) => {
          if (cancelled) return;
          setPrevVersions(vs);
          if (!versionManual) setVersionNumber(nextVersionNumber(vs));
        });
      })
      .catch(() => { if (!cancelled) setLoadError("Nepodarilo sa načítať projekt."); });
    return () => { cancelled = true; };
  }, [slug]);

  useEffect(() => { verRef.current?.focus(); }, []);

  const lastVersion = prevVersions.length > 0
    ? [...prevVersions].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0]
    : null;

  // v4.0.25: a loaded-and-dirty tree blocks founding (null = not yet loaded → don't block).
  const isDirty = gitStatus !== null && !gitStatus.clean;

  async function handleNexUpgrade(target: string) {
    if (!project) return;
    setNexBusy(true);
    try {
      await upgradeNexsharedApi(project.id, target);
      // Reflect the bump locally + dismiss (the new version will build on the chosen nex-shared).
      setNexStatus((s) => (s ? { ...s, current: target, behind: 0, up_to_date: true, changelog: [] } : s));
      setNexDismissed(true);
    } catch {
      // Best-effort: leave the prompt up so the Manažér can retry or dismiss.
    } finally {
      setNexBusy(false);
    }
  }

  async function refreshGitStatus() {
    if (!project) return;
    try {
      setGitStatus(await getGitStatusApi(project.id));
    } catch {
      /* best-effort — leave the last known status */
    }
  }

  // "Uložiť ich" — commit all pending changes (preserves work), then re-check the tree.
  async function handleCommitTree() {
    if (!project) return;
    setGitBusy(true);
    try {
      await commitGitApi(project.id);
      await refreshGitStatus();
    } catch {
      /* leave the guard up so the operator can retry or discard */
    } finally {
      setGitBusy(false);
    }
  }

  // "Zahodiť" — discard all pending changes (destructive; the guard confirms first).
  async function handleDiscardTree() {
    if (!project) return;
    setGitBusy(true);
    try {
      await discardGitApi(project.id);
      await refreshGitStatus();
    } catch {
      /* leave the guard up */
    } finally {
      setGitBusy(false);
    }
  }

  function validate(): boolean {
    const next: Record<string, string> = {};
    const v = versionNumber.trim();
    if (!v) {
      next.versionNumber = "Číslo verzie je povinné.";
    } else if (!/^\d+\.\d+\.\d+$/.test(v)) {
      // Audit Theme 5: without a format guard, "v1"/"verzia 1" passed and later silently broke the on-disk
      // spec path (docs/specs/versions/v<N>/…). Require bare semver so the version folder is always valid.
      next.versionNumber = 'Číslo verzie musí byť v tvare 1.2.3 (len čísla a bodky — bez písmena „v" a bez medzier).';
    }
    // STEP 2: Zadanie is OPTIONAL — a blank brief is valid; the Špecifikácia is then built from the
    // Riadiace-centrum conversation from scratch (no not-empty gate here).
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  // Step 1 (design §4.3): create the version row + persist the Zadanie to customer-requirements.md.
  // Reveals the "Spustiť tvorbu špecifikácie" action; does NOT auto-start the build ("no autopilot").
  async function handleSaveZadanie(e: React.FormEvent) {
    e.preventDefault();
    if (!project || !validate()) return;
    setFormError(null);
    setSaving(true);
    try {
      const v = await createVersion(project.id, {
        version_number: versionNumber.trim(),
        name: name.trim() || undefined,
        // The version's free-text intent mirrors the Zadanie so the version list shows a summary.
        description: zadanie.trim() || undefined,
        target_date: targetDate || undefined,
      });
      // Persist the brief to the spec tree the Príprava phase reads — ONLY when non-empty. A blank Zadanie
      // writes NO customer-requirements.md (STEP 2): the directive's "read it IF EXISTS" stays a clean
      // present/absent test, never present-but-empty.
      if (zadanie.trim()) await writeZadanie(v.id, zadanie.trim());
      setSavedVersion(v);
    } catch (err: unknown) {
      setFormError(humanizeApiError(err, "Uloženie Zadania zlyhalo"));
    } finally {
      setSaving(false);
    }
  }

  // Step 2 (design §2.1 / §4.3): begin the Príprava phase — pin the project+version (the AI Agent
  // tab + Vývoj board are pin-scoped), trigger the engine ``start`` action (which injects the
  // Príprava init prompt "Načítaj zadanie a začni prípravu špecifikácie"), then open the AI Agent
  // tab so the Manažér watches the interactive spec dialogue live.
  async function handleStart() {
    if (!project || !savedVersion) return;
    setFormError(null);
    setStarting(true);
    try {
      await postPipelineActionApi(savedVersion.id, { action: "start" });
      setSelectedProject({ slug: project.slug, name: project.name });
      setSelectedVersion({ versionId: savedVersion.id, versionNumber: savedVersion.version_number });
      navigate("/ai-agent");
    } catch (err: unknown) {
      setFormError(humanizeApiError(err, "Spustenie tvorby špecifikácie zlyhalo"));
      setStarting(false);
    }
  }

  if (loadError) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">
          {loadError}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-[var(--color-border-default)] flex items-center gap-3 bg-[var(--color-canvas)]">
        <button
          onClick={() => navigate(`/projects/${slug}`)}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 className="text-base font-bold text-[var(--color-text-primary)]">Nová verzia</h1>
          {project && (
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              {project.name}
              {lastVersion && (
                <>
                  {" · nadväzuje na "}
                  <span className="text-[var(--color-text-secondary)] font-mono">{lastVersion.version_number}</span>
                </>
              )}
            </p>
          )}
        </div>
      </div>

      {/* Scrollable form */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-xl mx-auto">
          {/* v4.0.25 dirty-tree guard — blocks founding until the tree is clean; resolve it first. */}
          {isDirty && !savedVersion && gitStatus && (
            <div className="mb-5">
              <DirtyTreeGuard
                status={gitStatus}
                busy={gitBusy}
                onCommit={handleCommitTree}
                onDiscard={handleDiscardTree}
              />
            </div>
          )}
          {/* nex-shared upgrade prompt — only once the tree is clean (resolve pending work first). */}
          {!isDirty && nexStatus && nexStatus.behind > 0 && !nexDismissed && !savedVersion && (
            <div className="mb-5">
              <NexsharedUpgradePrompt
                status={nexStatus}
                busy={nexBusy}
                onUpgrade={handleNexUpgrade}
                onStay={() => setNexDismissed(true)}
              />
            </div>
          )}
          <form onSubmit={handleSaveZadanie} noValidate className="space-y-5">

            {/* Version number + Name */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="block text-sm font-medium text-[var(--color-text-secondary)]">
                    Číslo verzie *
                  </label>
                  {!versionManual && versionNumber && !savedVersion && (
                    <span className="text-[10px] text-primary-400/70 font-normal">automaticky navrhnuté</span>
                  )}
                </div>
                <input
                  ref={verRef}
                  type="text"
                  placeholder="0.1.0"
                  autoComplete="off"
                  spellCheck={false}
                  disabled={!!savedVersion}
                  value={versionNumber}
                  onChange={(e) => {
                    setVersionNumber(e.target.value);
                    setVersionManual(true);
                    if (errors.versionNumber) setErrors((er) => ({ ...er, versionNumber: "" }));
                  }}
                  className={`${inputCls} font-mono disabled:opacity-60 ${errors.versionNumber ? "border-[var(--color-state-error-bg)]" : ""}`}
                />
                {errors.versionNumber && (
                  <p className="mt-1 text-xs text-[var(--color-status-error)]">{errors.versionNumber}</p>
                )}
                <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">Prvá verzia = 0.1.0 · 1.0.0 = prvé produkčné vydanie</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1">
                  Názov <span className="text-[var(--color-text-muted)] font-normal text-xs">(voliteľné)</span>
                </label>
                <input
                  type="text"
                  placeholder="napr. platobný modul"
                  value={name}
                  disabled={!!savedVersion}
                  onChange={(e) => setName(e.target.value)}
                  className={`${inputCls} disabled:opacity-60`}
                />
              </div>
            </div>

            {/* Target date */}
            <div>
              <label className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1">
                Cieľový dátum <span className="text-[var(--color-text-muted)] font-normal text-xs">(voliteľné)</span>
              </label>
              <input
                type="date"
                value={targetDate}
                disabled={!!savedVersion}
                onChange={(e) => setTargetDate(e.target.value)}
                className={`${inputCls} disabled:opacity-60`}
              />
            </div>

            {/* Zadanie — the free-text brief, the MAIN input (design §4.3). Saved to
                docs/specs/versions/v<N>/customer-requirements.md; the Príprava phase reads it. */}
            <div>
              <label className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1">
                Zadanie{" "}
                <span className="ml-1 text-[var(--color-text-muted)] font-normal text-xs">(nepovinné — brief; voľný text)</span>
              </label>
              <textarea
                lang="sk"
                spellCheck={false}
                rows={8}
                placeholder="Opíš, čo má verzia priniesť. Napr.: Pridať platobný modul cez Tatra banku. Zákazník potrebuje automatické párovanie platieb s faktúrami a emailové notifikácie. Cieľ: funkčné platby pre pilotného zákazníka do konca mája. (AI Agent zadanie systematizuje a v Príprave sa doptá na nejasnosti.)"
                value={zadanie}
                disabled={!!savedVersion}
                onChange={(e) => {
                  setZadanie(e.target.value);
                  if (errors.zadanie) setErrors((er) => ({ ...er, zadanie: "" }));
                }}
                className={`${inputCls} resize-none leading-relaxed disabled:opacity-60 ${errors.zadanie ? "border-[var(--color-state-error-bg)]" : ""}`}
              />
              {errors.zadanie ? (
                <p className="mt-1 text-xs text-[var(--color-status-error)]">{errors.zadanie}</p>
              ) : (
                <p className="text-[11px] text-[var(--color-text-muted)] mt-1.5">
                  Nepovinné. Ak Zadanie vyplníš, uloží sa ako vstup pre Prípravu; ak ho necháš prázdne,
                  Špecifikáciu postavíte od nuly v rozhovore v Riadiacom centre.
                </p>
              )}
            </div>

            {/* Error banner */}
            <ErrorNote
              error={formError}
              className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-3"
            />

            {/* Saved confirmation — shown after step 1 succeeds. */}
            {savedVersion && (
              <div className="flex items-center gap-2 rounded-lg bg-[var(--color-state-success-bg)] border border-[var(--color-state-success-bg)] p-3 text-sm text-[var(--color-state-success-fg)]">
                <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                Zadanie uložené pre verziu <span className="font-mono">{savedVersion.version_number}</span>. Spusti tvorbu
                špecifikácie alebo otvor verziu.
              </div>
            )}

            {/* v4.0.25: while the tree is dirty, founding is blocked — resolve the guard above first. */}
            {isDirty && !savedVersion && (
              <p className="text-[12px] text-[var(--color-state-warning-fg)]">
                Najprv vyrieš neuložené zmeny hore, potom môžeš založiť verziu.
              </p>
            )}

            {/* Actions */}
            {!savedVersion ? (
              // Step 1 — save the Zadanie.
              <div className="flex gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => navigate(`/projects/${slug}`)}
                  className="flex-1 px-4 py-2 text-sm text-[var(--color-text-secondary)] border border-[var(--color-border-default)] rounded-lg hover:bg-[var(--color-surface-hover)] transition-colors"
                >
                  Zrušiť
                </button>
                <button
                  type="submit"
                  disabled={saving || !project || isDirty}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
                >
                  {saving ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Ukladám…
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Uložiť Zadanie
                    </>
                  )}
                </button>
              </div>
            ) : (
              // Step 2 — begin Príprava (no autopilot — design §4.3).
              <div className="flex gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => navigate(`/projects/${slug}/versions/${savedVersion.id}`)}
                  className="flex-1 px-4 py-2 text-sm text-[var(--color-text-secondary)] border border-[var(--color-border-default)] rounded-lg hover:bg-[var(--color-surface-hover)] transition-colors"
                >
                  Otvoriť verziu
                </button>
                <button
                  type="button"
                  onClick={handleStart}
                  disabled={starting}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
                >
                  {starting ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Spúšťam…
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      Spustiť tvorbu špecifikácie
                    </>
                  )}
                </button>
              </div>
            )}

          </form>
        </div>
      </div>
    </div>
  );
}
