import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProjectApi, suggestPortApi } from "@/services/api/projects";
import { useAuthStore } from "@/store/authStore";
import type { ProjectCategory } from "@/types";

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
  const [description, setDescription] = useState("");
  const [backendPort, setBackendPort] = useState<string>("");
  const [frontendPort, setFrontendPort] = useState<string>("");
  const [dbPort, setDbPort] = useState<string>("");

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);

  const nameRef = useRef<HTMLInputElement>(null);

  // Auto-focus name on mount
  useEffect(() => { nameRef.current?.focus(); }, []);

  // Auto-suggest ports on mount
  useEffect(() => {
    Promise.all([
      suggestPortApi("backend").catch(() => null),
      suggestPortApi("frontend").catch(() => null),
      suggestPortApi("db").catch(() => null),
    ]).then(([be, fe, db]) => {
      if (be) setBackendPort(String(be.suggested_port));
      if (fe) setFrontendPort(String(fe.suggested_port));
      if (db) setDbPort(String(db.suggested_port));
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
      });
      navigate(`/projects/${project.slug}`);
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

            {/* GitHub repo */}
            <Field label="GitHub repository">
              <input
                type="text"
                placeholder="rauschiccsk/project-name"
                autoComplete="off"
                spellCheck={false}
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
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
                  { label: "Backend",  value: backendPort,  set: setBackendPort },
                  { label: "Frontend", value: frontendPort, set: setFrontendPort },
                  { label: "Database", value: dbPort,       set: setDbPort },
                ] as const).map(({ label, value, set }) => (
                  <div key={label}>
                    <label className="block text-xs text-slate-500 mb-1">{label}</label>
                    <input
                      type="number"
                      placeholder="9100"
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
