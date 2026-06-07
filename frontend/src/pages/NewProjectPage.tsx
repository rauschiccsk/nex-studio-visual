import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProjectApi, suggestPortBlockApi } from "@/services/api/projects";
import { getSystemSettingApi } from "@/services/api/systemSettings";
import { listUsersApi } from "@/services/api/users";
import { useAuthStore } from "@/store/authStore";
import type { ProjectCategory } from "@/types";
import type { UserRead } from "@/types/user";

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
        <label className="block text-sm font-medium text-slate-300">{label}</label>
        {hint}
      </div>
      {children}
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
}

// ─── Input styles ─────────────────────────────────────────────────────────────

const inputCls =
  "w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary-500 transition-colors";

const portInputCls =
  "w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-100 font-mono focus:outline-none focus:border-primary-500 transition-colors";

// ─── NewProjectPage ───────────────────────────────────────────────────────────

export default function NewProjectPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);

  const [category, setCategory] = useState<ProjectCategory>("singlemodule");
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
  const [enableCoordinator, setEnableCoordinator] = useState(true);
  const [enableCicd, setEnableCicd] = useState(false);
  const [fullSmoke, setFullSmoke] = useState(false);
  const [enableBranchProtection, setEnableBranchProtection] = useState(false);

  // CR-NS-012: notification owner picker. Empty = none (no notifications).
  const [users, setUsers] = useState<UserRead[]>([]);
  const [ownerId, setOwnerId] = useState<string>("");

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);

  const nameRef = useRef<HTMLInputElement>(null);

  // Auto-focus name on mount
  useEffect(() => { nameRef.current?.focus(); }, []);

  // Fetch users for the Owner picker; default to the current user when present.
  useEffect(() => {
    let cancelled = false;
    listUsersApi({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        setUsers(res.items);
        if (user?.id && res.items.some((u) => u.id === user.id)) {
          setOwnerId(user.id);
        }
      })
      .catch(() => { /* owner is optional — silently leave the picker empty */ });
    return () => { cancelled = true; };
  }, [user?.id]);

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
      })
      .catch(() => {
        // Registry exhausted or API unreachable — leave inputs empty,
        // the user can still enter ports manually.
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
    if (!name.trim()) next.name = "Project name is required.";
    if (!slug.trim()) next.slug = "Slug is required.";
    else if (!/^[a-z0-9-]+$/.test(slug)) next.slug = "Slug: lowercase letters, numbers and hyphens only.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setFormError("");
    setLoading(true);
    try {
      const project = await createProjectApi({
        name: name.trim(),
        slug: slug.trim(),
        category,
        description: description.trim(),
        repo_url: repo.trim() || null,
        backend_port: backendPort ? Number(backendPort) : null,
        frontend_port: frontendPort ? Number(frontendPort) : null,
        db_port: dbPort ? Number(dbPort) : null,
        created_by: user?.id ?? "",
        owner_id: ownerId || null,
        // F-004 flags
        enable_coordinator: enableCoordinator,
        enable_cicd: enableCicd,
        full_smoke: fullSmoke,
        enable_branch_protection: enableBranchProtection,
      });
      navigate(`/projects/${project.slug}`, {
        state: {
          justCreated: true,
          repoUrl: project.repo_url,
          backendPort: project.backend_port,
          frontendPort: project.frontend_port,
          dbPort: project.db_port,
        },
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Nepodarilo sa vytvoriť projekt.";
      setFormError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-slate-800 flex items-center gap-3 bg-slate-950">
        <button
          onClick={() => navigate("/projects")}
          className="text-slate-500 hover:text-slate-300 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-base font-bold text-slate-100">New Project</h1>
      </div>

      {/* Scrollable form */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-xl mx-auto">
          <form onSubmit={handleSubmit} noValidate className="space-y-5">

            {/* Category */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Project type</label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setCategory("singlemodule")}
                  className={`p-3 rounded-lg border text-left transition-colors ${
                    category === "singlemodule"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-slate-600 text-slate-400 hover:border-slate-500"
                  }`}
                >
                  <svg className="w-4 h-4 mb-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                  </svg>
                  <div className="text-sm font-medium">Single module</div>
                  <div className="text-[10px] opacity-70 mt-0.5">One repo, direct development</div>
                </button>
                <button
                  type="button"
                  onClick={() => setCategory("multimodule")}
                  className={`p-3 rounded-lg border text-left transition-colors ${
                    category === "multimodule"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-slate-600 text-slate-400 hover:border-slate-500"
                  }`}
                >
                  <svg className="w-4 h-4 mb-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                  </svg>
                  <div className="text-sm font-medium">Multi module</div>
                  <div className="text-[10px] opacity-70 mt-0.5">Multiple repos, complex project</div>
                </button>
              </div>
            </div>

            {/* Name */}
            <Field label="Project name *" error={errors.name}>
              <input
                ref={nameRef}
                type="text"
                placeholder="NEX Ledger"
                autoComplete="off"
                value={name}
                onChange={(e) => handleNameChange(e.target.value)}
                className={`${inputCls} ${errors.name ? "border-red-500/50" : ""}`}
              />
            </Field>

            {/* Slug */}
            <Field
              label="Slug *"
              error={errors.slug}
              hint={
                !slugManual && slug ? (
                  <span className="text-[10px] text-primary-400/70 font-normal">auto-generated</span>
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
                className={`${inputCls} font-mono ${errors.slug ? "border-red-500/50" : ""}`}
              />
            </Field>

            {/* GitHub repo — auto-filled as {github_org}/{slug} from ICC settings. */}
            <Field label="GitHub repository">
              <input
                type="text"
                placeholder={githubOrg ? `${githubOrg}/project-name` : "rauschiccsk/project-name"}
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
            <Field label="Description">
              <textarea
                rows={2}
                placeholder="Short project description…"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className={`${inputCls} resize-none`}
              />
            </Field>

            {/* Ports */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-slate-300">Ports</span>
                {(backendPort || frontendPort || dbPort) && (
                  <span className="flex items-center gap-1 text-[11px] text-primary-400/70">
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    Auto-suggested
                  </span>
                )}
              </div>
              <div className="grid grid-cols-3 gap-3 p-3 bg-slate-900/60 rounded-lg border border-slate-700">
                {([
                  { label: "Backend",   value: backendPort,   set: setBackendPort,   placeholder: "10100" },
                  { label: "Frontend",  value: frontendPort,  set: setFrontendPort,  placeholder: "10101" },
                  { label: "Database",  value: dbPort,        set: setDbPort,        placeholder: "10102" },
                ] as const).map(({ label, value, set, placeholder }) => (
                  <div key={label}>
                    <label className="block text-xs text-slate-500 mb-1">{label}</label>
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
              <label htmlFor="np-owner" className="block text-sm font-medium text-slate-300 mb-2">
                Owner <span className="text-slate-500 font-normal">(receives agent Telegram notifications)</span>
              </label>
              <select
                id="np-owner"
                value={ownerId}
                onChange={(e) => setOwnerId(e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
              >
                <option value="">— none —</option>
                {users.map((u) => {
                  const display = [u.first_name, u.last_name].filter(Boolean).join(" ") || u.username;
                  return (
                    <option key={u.id} value={u.id}>
                      {display} ({u.username})
                    </option>
                  );
                })}
              </select>
            </div>

            {/* F-004 Setup options */}
            <div className="space-y-2 rounded-lg border border-slate-800 p-4">
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">
                Setup options
              </h3>
              <label className="flex items-center gap-3 text-sm text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enableCoordinator}
                  onChange={(e) => setEnableCoordinator(e.target.checked)}
                  className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-primary-500 focus:ring-primary-500"
                />
                <span>Enable Koordinátor agent</span>
                <span className="text-xs text-slate-500">(default ON)</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enableCicd}
                  onChange={(e) => setEnableCicd(e.target.checked)}
                  className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-primary-500 focus:ring-primary-500"
                />
                <span>Enable CI/CD (GitHub Actions)</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={fullSmoke}
                  onChange={(e) => setFullSmoke(e.target.checked)}
                  className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-primary-500 focus:ring-primary-500"
                />
                <span>Full smoke test (build + up + /health, ~5-7 min)</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enableBranchProtection}
                  onChange={(e) => setEnableBranchProtection(e.target.checked)}
                  className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-primary-500 focus:ring-primary-500"
                />
                <span>Enable branch protection (require PR, no force push)</span>
              </label>
            </div>

            {/* Error banner */}
            {formError && (
              <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-3 text-sm text-red-400">
                {formError}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-1">
              <button
                type="button"
                onClick={() => navigate("/projects")}
                className="flex-1 px-4 py-2 text-sm text-slate-400 border border-slate-700 rounded-lg hover:bg-slate-800 transition-colors"
              >
                Cancel
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
                    Creating…
                  </>
                ) : (
                  <>
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Create project
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
