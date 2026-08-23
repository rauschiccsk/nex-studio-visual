import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProjectApi, suggestPortBlockApi } from "@/services/api/projects";
import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getSystemSettingApi } from "@/services/api/systemSettings";
import { listUsersApi } from "@/services/api/users";
import { useAuthStore } from "@/store/authStore";
import type { ProjectAuthMode, ProjectType } from "@/types";
import type { UserRead } from "@/types/user";
import { isAdminAccount } from "@/services/permissions";

// ─── Slug helper ─────────────────────────────────────────────────────────────

function nameToSlug(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

// ─── Field component ─────────────────────────────────────────────────────────

interface FieldProps {
  label: string;
  error?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}

function Field({ label, error, hint, children }: FieldProps) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <label className="block text-sm font-medium text-[var(--color-text-secondary)]">{label}</label>
        {hint}
      </div>
      {children}
      {error && <p className="mt-1 text-xs text-[var(--color-status-error)]">{error}</p>}
    </div>
  );
}

// ─── Input styles ─────────────────────────────────────────────────────────────

const inputCls =
  "w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary-500 transition-colors";

const portInputCls =
  "w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-primary-500 transition-colors";

// ─── NewProjectPage ───────────────────────────────────────────────────────────

export default function NewProjectPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);

  // Archetype (CR-V2-005, design §4.2): the preset surface composition that replaces the retired
  // single/multi-module category. Štandardný = BE + app-FE; Web = BE + admin-FE + public-site.
  const [type, setType] = useState<ProjectType>("standard");
  // Auth mode (CR-V2-005, design §4.2): shapes the project's auth structure (BE login + FE login flow).
  // Audit Theme 5: defaults to the common "password" instead of forcing a non-expert to make an uninformed
  // choice up front (they can still switch to token). Submit is never blocked on this now.
  const [authMode, setAuthMode] = useState<ProjectAuthMode>("password");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugManual, setSlugManual] = useState(false);
  const [repo, setRepo] = useState("");
  const [repoManual, setRepoManual] = useState(false);
  const [githubOrg, setGithubOrg] = useState<string>("");
  const [description, setDescription] = useState("");
  const [backendPort, setBackendPort] = useState<string>("");
  const [frontendPort, setFrontendPort] = useState<string>("");
  const [dbPort, setDbPort] = useState<string>("");

  // F-004 flags
  const [enableCicd, setEnableCicd] = useState(false);
  const [fullSmoke, setFullSmoke] = useState(false);
  const [enableBranchProtection, setEnableBranchProtection] = useState(false);
  // STEP 6 (R9): "Vývoj na zákazku" — create-only flag, the only switch that later permits deviating from
  // the unified company design. Inert data in STEP 6 (no behaviour binds to it yet). Default unchecked.
  const [customDevelopment, setCustomDevelopment] = useState(false);

  // CR-NS-012: notification owner picker. Empty = none (no notifications).
  const [users, setUsers] = useState<UserRead[]>([]);
  const [ownerId, setOwnerId] = useState<string>("");

  // Only the admin account can list users (GET /users), so only it gets a picker for the notification
  // recipient; everyone else's project notifies themselves. NOTE this has nothing to do with who OWNS
  // the project — that is always the person creating it and is not selectable.
  const canPickOwner = isAdminAccount(user);
  const ownerSelfDisplay = user
    ? [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username
    : "";

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [portsNote, setPortsNote] = useState<string>("");
  const [formError, setFormError] = useState<HumanError | null>(null);
  const [loading, setLoading] = useState(false);

  const nameRef = useRef<HTMLInputElement>(null);

  // Auto-focus name on mount
  useEffect(() => { nameRef.current?.focus(); }, []);

  // Owner defaults to the current user immediately — independent of the (ri-only) user-list fetch, so a
  // non-admin never lands on an empty "— žiadny —" picker (v4.0.34).
  useEffect(() => {
    if (user?.id) setOwnerId((prev) => prev || user.id);
  }, [user?.id]);

  // Fetch users for the owner PICKER (admins only — GET /users is ri-only). A non-admin 403s here and
  // simply keeps the self default above; the field renders read-only.
  useEffect(() => {
    let cancelled = false;
    listUsersApi({ limit: 100 })
      .then((res) => {
        if (!cancelled) setUsers(res.items);
      })
      .catch(() => {
        /* non-admin: no user list — owner stays self (defaulted above) */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the github_org ICC setting — used to auto-fill repo_url as
  // "{github_org}/{slug}". Fails silently when the endpoint is
  // unreachable; the user can still enter repo_url manually.
  useEffect(() => {
    getSystemSettingApi("github_org")
      .then((s) => setGithubOrg(s.value))
      .catch(() => {
        /* leave empty; user types repo_url manually */
      });
  }, []);

  // Whenever github_org or slug changes and the user has not manually
  // edited the repo field, regenerate the auto-filled value.
  useEffect(() => {
    if (repoManual) return;
    if (!githubOrg || !slug) {
      setRepo("");
      return;
    }
    setRepo(`${githubOrg}/${slug}`);
  }, [githubOrg, slug, repoManual]);

  // Auto-suggest the three ports of a single free block on mount.
  // The backend allocates a contiguous 10-port block per project per
  // DECISIONS.md D-020 — we fill +0 backend, +1 frontend, +2 db;
  // the remaining +3..+9 stay as the project's reserve.
  useEffect(() => {
    suggestPortBlockApi()
      .then((block) => {
        setBackendPort(String(block.base));
        setFrontendPort(String(block.base + 1));
        setDbPort(String(block.base + 2));
        // The suggestion can succeed WITH a caveat — most often that no reserved ranges are configured,
        // so it could not be checked against them. That caveat is written in Slovak for the Manager and
        // was being thrown away here, leaving a confidently-filled form with an unspoken condition.
        setPortsNote(block.warnings?.length ? block.warnings.join(" ") : "");
      })
      .catch((err: unknown) => {
        // Say WHY the ports are blank. The endpoint is now fail-closed: rather than offering a port
        // it cannot verify is free, it answers 503 when it cannot see the host (the docker socket
        // momentarily gone is a documented landmine on this machine). Swallowing that left three
        // empty fields with no explanation, and "free" is exactly the thing a person cannot check by
        // hand — the cockpit has already double-booked a port once.
        setPortsNote(
          humanizeApiError(err, "Voľné porty sa nepodarilo zistiť").message +
            " Zadaj ich ručne, alebo to skús znova o chvíľu.",
        );
      });
  }, []);

  // Auto-generate slug from name unless manually edited
  function handleNameChange(v: string) {
    setName(v);
    if (!slugManual) setSlug(nameToSlug(v));
    if (errors.name) setErrors((e) => ({ ...e, name: "" }));
  }

  function handleSlugChange(v: string) {
    setSlug(v);
    setSlugManual(true);
    if (errors.slug) setErrors((e) => ({ ...e, slug: "" }));
  }

  function validate(): boolean {
    const next: Record<string, string> = {};
    if (!name.trim()) next.name = "Názov projektu je povinný.";
    if (!slug.trim()) next.slug = "Identifikátor je povinný.";
    else if (!/^[a-z0-9-]+$/.test(slug))
      next.slug = "Identifikátor: iba malé písmená, čísla a pomlčky (napr. nex-ledger).";
    // Auth mode is MANDATORY (design §4.2) — it shapes the project's auth structure.
    if (!authMode) next.authMode = "Spôsob prihlásenia je povinný.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setFormError(null);
    setLoading(true);
    try {
      const project = await createProjectApi({
        name: name.trim(),
        slug: slug.trim(),
        type,
        // Guarded by validate() — authMode is non-empty here.
        auth_mode: authMode as ProjectAuthMode,
        description: description.trim(),
        repo_url: repo.trim() || null,
        backend_port: backendPort ? Number(backendPort) : null,
        frontend_port: frontendPort ? Number(frontendPort) : null,
        db_port: dbPort ? Number(dbPort) : null,
        created_by: user?.id ?? "",
        owner_id: ownerId || null,
        // F-004 flags
        enable_cicd: enableCicd,
        full_smoke: fullSmoke,
        enable_branch_protection: enableBranchProtection,
        // STEP 6 (R9): create-only "Vývoj na zákazku" flag.
        custom_development_enabled: customDevelopment,
      });
      navigate(`/projects/${project.slug}`, {
        state: {
          justCreated: true,
          repoUrl: project.repo_url,
          backendPort: project.backend_port,
          frontendPort: project.frontend_port,
          dbPort: project.db_port,
          // What did not finish. The founding steps are best-effort and never block the create, so the
          // project really does exist — but CI, the runner or the smoke test may be waiting for a hand.
          setupWarnings: project.setup_warnings ?? [],
        },
      });
    } catch (err: unknown) {
      // Audit Theme 2: the raw backend detail (English "…D-020…", or an object-shaped detail rendering as
      // "[object Object]") used to surface verbatim. Frame it in plain Slovak; the raw text stays as the detail.
      // Keep the WHOLE HumanError, not just its opening sentence. Storing `.message` alone threw away
      // the `.detail` carrying the backend's real text, so every HTTP 500 — a missing template, a git
      // failure, a refused slug — read as the same "skús to o chvíľu znova", which is false advice for
      // all of them. ErrorNote renders the plain-Slovak line and tucks the technical text behind a
      // disclosure, exactly as every other screen in the cockpit does.
      setFormError(humanizeApiError(err, "Projekt sa nepodarilo vytvoriť"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-[var(--color-border-default)] flex items-center gap-3 bg-[var(--color-canvas)]">
        <button
          onClick={() => navigate("/projects")}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Nový projekt</h1>
      </div>

      {/* Scrollable form */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-xl mx-auto">
          <form onSubmit={handleSubmit} noValidate className="space-y-5">

            {/* Archetype (CR-V2-005, design §4.2): Štandardný / Web */}
            <div>
              <label className="block text-sm font-medium text-[var(--color-text-secondary)] mb-2">Typ projektu</label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setType("standard")}
                  className={`p-3 rounded-lg border text-left transition-colors ${
                    type === "standard"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-[var(--color-border-strong)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-strong)]"
                  }`}
                >
                  <svg className="w-4 h-4 mb-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                  </svg>
                  <div className="text-sm font-medium">Štandardný</div>
                  <div className="text-[10px] opacity-70 mt-0.5">Backend + aplikačné rozhranie</div>
                </button>
                <button
                  type="button"
                  onClick={() => setType("web")}
                  className={`p-3 rounded-lg border text-left transition-colors ${
                    type === "web"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-[var(--color-border-strong)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-strong)]"
                  }`}
                >
                  <svg className="w-4 h-4 mb-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-9a14.5 14.5 0 000 18m0-18a14.5 14.5 0 010 18M3.6 9h16.8M3.6 15h16.8" />
                  </svg>
                  <div className="text-sm font-medium">Web</div>
                  <div className="text-[10px] opacity-70 mt-0.5">Backend + admin + verejná stránka</div>
                </button>
              </div>
            </div>

            {/* Auth mode (CR-V2-005, design §4.2): MANDATORY — shapes the project's auth structure. */}
            <div>
              <label className="block text-sm font-medium text-[var(--color-text-secondary)] mb-2">
                Spôsob prihlásenia <span className="font-normal text-[var(--color-text-muted)]">(predvolené: meno a heslo)</span>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setAuthMode("password");
                    if (errors.authMode) setErrors((e) => ({ ...e, authMode: "" }));
                  }}
                  className={`p-3 rounded-lg border text-left transition-colors ${
                    authMode === "password"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : `text-[var(--color-text-secondary)] hover:border-[var(--color-border-strong)] ${
                          errors.authMode ? "border-[var(--color-status-error)]" : "border-[var(--color-border-strong)]"
                        }`
                  }`}
                >
                  <svg className="w-4 h-4 mb-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 11c0-1.105.895-2 2-2s2 .895 2 2m-9 0h10a2 2 0 012 2v6a2 2 0 01-2 2H7a2 2 0 01-2-2v-6a2 2 0 012-2zm5-7a4 4 0 014 4v3H8V8a4 4 0 014-4z" />
                  </svg>
                  <div className="text-sm font-medium">Meno a heslo</div>
                  <div className="text-[10px] opacity-70 mt-0.5">Používateľ sa prihlási menom a heslom. Bežná voľba.</div>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAuthMode("token");
                    if (errors.authMode) setErrors((e) => ({ ...e, authMode: "" }));
                  }}
                  className={`p-3 rounded-lg border text-left transition-colors ${
                    authMode === "token"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : `text-[var(--color-text-secondary)] hover:border-[var(--color-border-strong)] ${
                          errors.authMode ? "border-[var(--color-status-error)]" : "border-[var(--color-border-strong)]"
                        }`
                  }`}
                >
                  <svg className="w-4 h-4 mb-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                  </svg>
                  <div className="text-sm font-medium">Token</div>
                  <div className="text-[10px] opacity-70 mt-0.5">Aplikácia sa otvára odkazom s tokenom, bez hesla.</div>
                </button>
              </div>
              {errors.authMode && <p className="mt-1 text-xs text-[var(--color-status-error)]">{errors.authMode}</p>}
            </div>

            {/* Name */}
            <Field label="Názov projektu *" error={errors.name}>
              <input
                spellCheck={false}
                ref={nameRef}
                type="text"
                placeholder="NEX Ledger"
                autoComplete="off"
                value={name}
                onChange={(e) => handleNameChange(e.target.value)}
                className={`${inputCls} ${errors.name ? "border-[var(--color-status-error)]" : ""}`}
              />
            </Field>

            {/* Slug */}
            <Field
              label="Identifikátor (slug) *"
              error={errors.slug}
              hint={
                !slugManual && slug ? (
                  <span className="text-[10px] text-primary-400/70 font-normal">automaticky generované</span>
                ) : null
              }
            >
              <input
                type="text"
                placeholder="nex-ledger"
                autoComplete="off"
                spellCheck={false}
                value={slug}
                onChange={(e) => handleSlugChange(e.target.value)}
                className={`${inputCls} font-mono ${errors.slug ? "border-[var(--color-status-error)]" : ""}`}
              />
              <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                Krátky názov v adresách, napr. nex-ledger.
              </p>
            </Field>

            {/* GitHub repo — auto-filled as {github_org}/{slug} from ICC settings. */}
            <Field label="GitHub úložisko">
              <input
                type="text"
                // The org comes from the github_org setting. When it could not be read, show the
                // SHAPE of the value — never a hardcoded organisation name, which read as a fact
                // about this install and was wrong for anyone who configured a different one.
                placeholder={githubOrg ? `${githubOrg}/nazov-projektu` : "organizacia/nazov-projektu"}
                autoComplete="off"
                spellCheck={false}
                value={repo}
                onChange={(e) => {
                  setRepo(e.target.value);
                  setRepoManual(true);
                }}
                className={`${inputCls} font-mono`}
              />
            </Field>

            {/* Description */}
            <Field label="Popis">
              <textarea
                lang="sk"
                spellCheck={false}
                rows={2}
                placeholder="Krátky popis projektu…"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className={`${inputCls} resize-none`}
              />
            </Field>

            {/* Ports */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-[var(--color-text-secondary)]">Porty</span>
                {portsNote && !backendPort && (
                  <span className="text-[11px] text-[var(--color-status-warning)]">{portsNote}</span>
                )}
                {(backendPort || frontendPort || dbPort) && (
                  <span className="flex items-center gap-1 text-[11px] text-primary-400/70">
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    automaticky navrhnuté
                  </span>
                )}
              </div>
              <div className="grid grid-cols-3 gap-3 p-3 bg-[var(--color-surface-hover)] rounded-lg border border-[var(--color-border-default)]">
                {([
                  { label: "Backend",   value: backendPort,   set: setBackendPort,   placeholder: "10100" },
                  { label: "Frontend",  value: frontendPort,  set: setFrontendPort,  placeholder: "10101" },
                  { label: "Databáza",  value: dbPort,        set: setDbPort,        placeholder: "10102" },
                ] as const).map(({ label, value, set, placeholder }) => (
                  <div key={label}>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">{label}</label>
                    <input
                      type="number"
                      placeholder={placeholder}
                      min={1}
                      max={65535}
                      value={value}
                      onChange={(e) => set(e.target.value)}
                      className={portInputCls}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* CR-NS-012 — notification owner */}
            <div>
              {/* Was labelled "Vlastník", which it is not. The project belongs to whoever creates it and
                  that cannot be reassigned here; this field only picks who the agent writes to. The old
                  label put the word "owner" on the one thing in the system that is NOT ownership. */}
              <label htmlFor="np-owner" className="block text-sm font-medium text-[var(--color-text-secondary)] mb-2">
                Komu chodia správy od agenta{" "}
                <span className="text-[var(--color-text-muted)] font-normal">(Telegram)</span>
              </label>
              {canPickOwner ? (
                <select
                  id="np-owner"
                  value={ownerId}
                  onChange={(e) => setOwnerId(e.target.value)}
                  className="w-full bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded-lg px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500"
                >
                  {/* No "— žiadny —" option. Picking it did not mean "no notifications": the service
                      enforces an invariant that a project ALWAYS has a recipient (CR-NS-074) and
                      silently rewrote the empty choice to the creator. The form offered a state the
                      system does not have. */}
                  {users.map((u) => {
                    const display = [u.first_name, u.last_name].filter(Boolean).join(" ") || u.username;
                    return (
                      <option key={u.id} value={u.id}>
                        {display} ({u.username})
                      </option>
                    );
                  })}
                </select>
              ) : (
                // Everyone else notifies themselves. The project is theirs either way — that is decided by
                // who creates it, not by this field.
                <div
                  id="np-owner"
                  className="w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-3 py-2 text-sm text-[var(--color-text-primary)]"
                >
                  {ownerSelfDisplay} <span className="text-[var(--color-text-muted)]">(ty)</span>
                </div>
              )}
            </div>

            {/* F-004 Setup options */}
            <div className="space-y-2 rounded-lg border border-[var(--color-border-default)] p-4">
              <h3 className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase tracking-wide">
                Možnosti nastavenia
              </h3>
              <label className="flex items-center gap-3 text-sm text-[var(--color-text-primary)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={enableCicd}
                  onChange={(e) => setEnableCicd(e.target.checked)}
                  className="w-4 h-4 rounded border-[var(--color-border-default)] bg-[var(--color-canvas)] text-primary-500 focus:ring-primary-500"
                />
                {/* Said what it DOES. The CI workflow this ships (templates/github-actions-workflow.yml)
                    runs lint, type-check, tests and a Docker build — it has no deploy job at all, so the
                    old "a nasadiť" promised a step that was never going to happen and the Manager would
                    wait for a deployment that never came. */}
                <span>Automaticky kontrolovať a zostaviť po každej zmene (bez nasadenia)</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--color-text-primary)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={fullSmoke}
                  onChange={(e) => setFullSmoke(e.target.checked)}
                  className="w-4 h-4 rounded border-[var(--color-border-default)] bg-[var(--color-canvas)] text-primary-500 focus:ring-primary-500"
                />
                <span>Úplná kontrola po zostavení (spustí aplikáciu a overí, že beží — ~5–7 min)</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--color-text-primary)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={enableBranchProtection}
                  onChange={(e) => setEnableBranchProtection(e.target.checked)}
                  className="w-4 h-4 rounded border-[var(--color-border-default)] bg-[var(--color-canvas)] text-primary-500 focus:ring-primary-500"
                />
                <span>Chrániť hlavnú vetvu (zmeny len cez schválenú žiadosť, bez prepisovania histórie)</span>
              </label>
              {/* Disabled, with the reason on screen — not hidden, and not left working-looking. The value
                  is stored on the project and read by NOTHING: no screen, no agent brief, no generator
                  consults it, so ticking it permitted exactly nothing while claiming to permit a deviation
                  from the company design. A control that does nothing is worse than an absent one, because
                  the Manager plans around it. */}
              <label
                className="flex items-center gap-3 text-sm text-[var(--color-text-muted)] cursor-not-allowed"
                title="Zatiaľ nie je zapojené — hodnota by sa uložila, ale nič sa ňou neriadi."
              >
                <input
                  type="checkbox"
                  checked={customDevelopment}
                  onChange={(e) => setCustomDevelopment(e.target.checked)}
                  disabled
                  className="w-4 h-4 rounded border-[var(--color-border-default)] bg-[var(--color-canvas)] text-primary-500 focus:ring-primary-500 disabled:opacity-50"
                />
                <span>
                  Vývoj na zákazku (odchýlka od jednotného firemného dizajnu) —{" "}
                  <span className="italic">zatiaľ nezapojené</span>
                </span>
              </label>
            </div>

            {/* Error banner */}
            {formError && (
              <ErrorNote
                error={formError}
                className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-3"
              />
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-1">
              <button
                type="button"
                onClick={() => navigate("/projects")}
                className="flex-1 px-4 py-2 text-sm text-[var(--color-text-secondary)] border border-[var(--color-border-default)] rounded-lg hover:bg-[var(--color-surface-hover)] transition-colors"
              >
                Zrušiť
              </button>
              <button
                type="submit"
                disabled={loading}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
              >
                {loading ? (
                  <>
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Vytváram…
                  </>
                ) : (
                  <>
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Vytvoriť projekt
                  </>
                )}
              </button>
            </div>

          </form>
        </div>
      </div>
    </div>
  );
}
