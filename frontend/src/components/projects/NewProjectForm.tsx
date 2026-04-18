/**
 * New Project form — visual design matching NEX Command NewProjectDialog.
 *
 * Key behaviours:
 *   - Category selector: icon-button grid (singlemodule / multimodule)
 *   - Port auto-suggest: fires on slug change via /ports/suggest API,
 *     fills BE/FE/DB with 3 consecutive ports, shows ⚡ indicator
 *   - PortField component: per-field suggest + availability check on blur
 *   - Slug auto-generate from name on blur
 *   - GitHub repo auto-derive: short format ``rauschiccsk/{slug}``
 *   - Dark-first styling (bg-gray-900 / border-gray-600 / text-gray-100)
 */

import { useState, useCallback, useEffect, useRef, type FormEvent } from "react";
import { Package, Layers, Zap } from "lucide-react";

import type {
  ProjectCreationFormData,
  ProjectCategory,
  SlugValidationError,
} from "@/types";
import PortField from "./PortField";
import { suggestNextAvailablePort } from "@/services/api/port-registry";

// ---------------------------------------------------------------------------
// Category options
// ---------------------------------------------------------------------------

const CATEGORY_OPTIONS: {
  value: ProjectCategory;
  label: string;
  icon: typeof Package;
  desc: string;
}[] = [
  {
    value: "singlemodule",
    label: "Single module",
    icon: Package,
    desc: "Jeden repozitár, priamy vývoj",
  },
  {
    value: "multimodule",
    label: "Multi module",
    icon: Layers,
    desc: "Viac repozitárov, komplexný projekt",
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GITHUB_ORG = "rauschiccsk";

/** Regex for short ``org/repo`` format. */
const GITHUB_REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

function toSlug(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Returns short ``rauschiccsk/{slug}`` form. */
function deriveRepo(slug: string): string {
  return slug ? `${GITHUB_ORG}/${slug}` : "";
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NewProjectFormProps {
  /** Called when the form passes client-side validation. */
  onSubmit: (data: ProjectCreationFormData) => void;
  /** Async slug uniqueness check — returns null if valid, error otherwise. */
  onValidateSlug?: (slug: string) => Promise<SlugValidationError | null>;
  /** Disables submit and shows loading state. */
  loading?: boolean;
  /** Server-side error message displayed above the submit button. */
  error?: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function NewProjectForm({
  onSubmit,
  onValidateSlug,
  loading = false,
  error = null,
}: NewProjectFormProps) {
  // -- Form state -----------------------------------------------------------
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [category, setCategory] = useState<ProjectCategory>("singlemodule");
  const [description, setDescription] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [backendPort, setBackendPort] = useState("");
  const [frontendPort, setFrontendPort] = useState("");
  const [dbPort, setDbPort] = useState("");
  const [portsAutoSuggested, setPortsAutoSuggested] = useState(false);

  // -- Derivation flags -----------------------------------------------------
  const [slugTouchedByUser, setSlugTouchedByUser] = useState(false);
  const [repoTouchedByUser, setRepoTouchedByUser] = useState(false);
  const portsTouchedByUser = useRef(false);

  // -- Validation state -----------------------------------------------------
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [slugError, setSlugError] = useState<string | null>(null);
  const [repoError, setRepoError] = useState<string | null>(null);

  const slugTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // -- Slug auto-generation on name blur ------------------------------------
  const handleNameBlur = useCallback(() => {
    setTouched((t) => ({ ...t, name: true }));
    if (!slugTouchedByUser) {
      const generated = toSlug(name);
      setSlug(generated);
      if (!repoTouchedByUser) {
        setGithubRepo(deriveRepo(generated));
      }
    }
  }, [name, slugTouchedByUser, repoTouchedByUser]);

  // -- Slug change (manual edit) -------------------------------------------
  const handleSlugChange = useCallback(
    (value: string) => {
      setSlugTouchedByUser(true);
      setSlug(value);
      if (!repoTouchedByUser) {
        setGithubRepo(deriveRepo(value));
      }
    },
    [repoTouchedByUser],
  );

  // -- GitHub repo change (manual edit) ------------------------------------
  const handleRepoChange = useCallback((value: string) => {
    setRepoTouchedByUser(true);
    setGithubRepo(value);
  }, []);

  // -- Port auto-suggest on slug change ------------------------------------
  useEffect(() => {
    if (!slug || portsTouchedByUser.current) return;

    let cancelled = false;
    suggestNextAvailablePort("backend")
      .then((p) => {
        if (cancelled) return;
        setBackendPort(String(p));
        setFrontendPort(String(p + 1));
        setDbPort(String(p + 2));
        setPortsAutoSuggested(true);
      })
      .catch(() => {
        // API unavailable — leave fields empty, user can fill manually
      });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  // -- Debounced slug validation -------------------------------------------
  useEffect(() => {
    if (slugTimerRef.current) clearTimeout(slugTimerRef.current);
    setSlugError(null);
    if (!slug || !onValidateSlug) return;

    slugTimerRef.current = setTimeout(async () => {
      const result = await onValidateSlug(slug);
      setSlugError(result?.message ?? null);
    }, 400);

    return () => {
      if (slugTimerRef.current) clearTimeout(slugTimerRef.current);
    };
  }, [slug, onValidateSlug]);

  // -- Inline GitHub repo format validation --------------------------------
  useEffect(() => {
    if (!githubRepo) {
      setRepoError(null);
      return;
    }
    setRepoError(GITHUB_REPO_RE.test(githubRepo) ? null : "Formát: org/repo");
  }, [githubRepo]);

  // -- Client-side validation -----------------------------------------------
  const nameError = touched.name && name.trim() === "" ? "Názov je povinný." : null;
  const slugEmpty = touched.slug && slug.trim() === "" ? "Slug je povinný." : null;
  const hasErrors = !!nameError || !!slugEmpty || !!slugError || !!repoError;

  // -- Submit ---------------------------------------------------------------
  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched({ name: true, slug: true });
    if (name.trim() === "" || slug.trim() === "") return;
    if (hasErrors) return;

    onSubmit({
      name: name.trim(),
      slug: slug.trim(),
      category,
      description: description.trim(),
      github_repo: githubRepo.trim(),
      backend_port: backendPort ? parseInt(backendPort, 10) : null,
      frontend_port: frontendPort ? parseInt(frontendPort, 10) : null,
      db_port: dbPort ? parseInt(dbPort, 10) : null,
    });
  }

  // -- Input class helper ---------------------------------------------------
  const inputClass = (hasErr: boolean) =>
    `w-full rounded-lg border px-3 py-2 text-sm bg-gray-900 text-gray-100 focus:outline-none focus:border-primary ${
      hasErr ? "border-red-500" : "border-gray-600"
    }`;

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      data-testid="new-project-form"
      className="space-y-5"
    >
      {/* Category — icon-button grid */}
      <div>
        <label className="mb-2 block text-sm font-medium text-gray-300">
          Typ projektu
        </label>
        <div className="grid grid-cols-2 gap-2" data-testid="project-category">
          {CATEGORY_OPTIONS.map(({ value, label, icon: Icon, desc }) => (
            <button
              key={value}
              type="button"
              onClick={() => setCategory(value)}
              disabled={loading}
              className={`p-3 rounded-lg border text-left transition-colors ${
                category === value
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-gray-600 text-gray-400 hover:border-gray-500"
              }`}
              data-testid={`category-${value}`}
            >
              <Icon className="w-4 h-4 mb-1" />
              <div className="text-sm font-medium">{label}</div>
              <div className="text-[10px] opacity-70">{desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Name */}
      <div>
        <label
          htmlFor="project-name"
          className="mb-1 block text-sm font-medium text-gray-300"
        >
          Názov projektu *
        </label>
        <input
          id="project-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={handleNameBlur}
          disabled={loading}
          placeholder="NEX Ledger"
          className={inputClass(!!nameError)}
          data-testid="project-name"
        />
        {nameError && (
          <p className="mt-1 text-xs text-red-500" role="alert">
            {nameError}
          </p>
        )}
      </div>

      {/* Slug */}
      <div>
        <label
          htmlFor="project-slug"
          className="mb-1 block text-sm font-medium text-gray-300"
        >
          Slug *
        </label>
        <input
          id="project-slug"
          type="text"
          value={slug}
          onChange={(e) => handleSlugChange(e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, slug: true }))}
          disabled={loading}
          placeholder="nex-ledger"
          className={inputClass(!!slugEmpty || !!slugError)}
          data-testid="project-slug"
        />
        {slugEmpty && (
          <p className="mt-1 text-xs text-red-500" role="alert">
            {slugEmpty}
          </p>
        )}
        {slugError && (
          <p
            className="mt-1 text-xs text-red-500"
            role="alert"
            data-testid="slug-error"
          >
            {slugError}
          </p>
        )}
      </div>

      {/* GitHub Repo */}
      <div>
        <label
          htmlFor="project-repo"
          className="mb-1 block text-sm font-medium text-gray-300"
        >
          GitHub repozitár
        </label>
        <input
          id="project-repo"
          type="text"
          value={githubRepo}
          onChange={(e) => handleRepoChange(e.target.value)}
          disabled={loading}
          placeholder={`${GITHUB_ORG}/nazov-projektu`}
          className={inputClass(!!repoError)}
          data-testid="project-repo"
        />
        {repoError && (
          <p
            className="mt-1 text-xs text-red-500"
            role="alert"
            data-testid="repo-error"
          >
            {repoError}
          </p>
        )}
      </div>

      {/* Description */}
      <div>
        <label
          htmlFor="project-description"
          className="mb-1 block text-sm font-medium text-gray-300"
        >
          Popis
        </label>
        <textarea
          id="project-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={loading}
          rows={2}
          placeholder="Krátky popis projektu..."
          className="w-full rounded-lg border border-gray-600 px-3 py-2 text-sm bg-gray-900 text-gray-100 focus:outline-none focus:border-primary resize-none"
          data-testid="project-description"
        />
      </div>

      {/* Ports */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-gray-300">Porty</span>
          {portsAutoSuggested && (
            <span className="flex items-center gap-1 text-[11px] text-primary/70">
              <Zap className="w-3 h-3" />
              Automaticky navrhnuté porty
            </span>
          )}
        </div>
        <div className="grid grid-cols-3 gap-3 p-3 bg-gray-900/50 rounded-lg border border-gray-700">
          <PortField
            label="Backend"
            type="backend"
            value={backendPort}
            onChange={(v) => {
              portsTouchedByUser.current = true;
              setPortsAutoSuggested(false);
              setBackendPort(v);
            }}
            placeholder="9100"
            disabled={loading}
            testId="backend-port"
          />
          <PortField
            label="Frontend"
            type="frontend"
            value={frontendPort}
            onChange={(v) => {
              portsTouchedByUser.current = true;
              setPortsAutoSuggested(false);
              setFrontendPort(v);
            }}
            placeholder="9101"
            disabled={loading}
            testId="frontend-port"
          />
          <PortField
            label="Databáza"
            type="db"
            value={dbPort}
            onChange={(v) => {
              portsTouchedByUser.current = true;
              setPortsAutoSuggested(false);
              setDbPort(v);
            }}
            placeholder="9102"
            disabled={loading}
            testId="db-port"
          />
        </div>
      </div>

      {/* Server error banner */}
      {error && (
        <div
          className="rounded-lg bg-red-500/10 border border-red-500/30 p-3 text-sm text-red-400"
          role="alert"
          data-testid="form-error"
        >
          {error}
        </div>
      )}

      {/* Submit */}
      <button
        type="submit"
        disabled={loading || hasErrors}
        className="w-full flex items-center justify-center px-4 py-2 bg-primary hover:bg-primary/90 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        data-testid="submit-button"
      >
        {loading ? "Vytváram projekt\u2026" : "Vytvori\u0165 projekt"}
      </button>
    </form>
  );
}
