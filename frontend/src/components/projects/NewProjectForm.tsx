/**
 * Reusable "New Project" form component — single screen CRUD.
 *
 * Renders a controlled form with all fields required by
 * {@link ProjectCreationFormData}.  Inline validation includes:
 *   - Slug uniqueness check (debounced, calls parent callback)
 *   - Port availability check (debounced, calls parent callback)
 *   - GitHub repo format validation (``https://github.com/org/repo``)
 *   - Auto-generate slug from name on blur
 *   - Auto-derive github_repo as ``https://github.com/rauschiccsk/{slug}``
 *     (stops auto-derive when user manually edits — ``repoTouchedByUser``)
 *
 * The form is presentation-only: the parent page component supplies
 * ``onSubmit``, ``onValidateSlug``, ``onValidatePort`` callbacks and
 * controls loading / server-error state.
 */

import { useState, useCallback, useEffect, useRef, type FormEvent } from "react";

import type {
  ProjectCreationFormData,
  ProjectCategory,
  PortValidationError,
  SlugValidationError,
} from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NewProjectFormProps {
  /** Called when the form passes client-side validation. */
  onSubmit: (data: ProjectCreationFormData) => void;
  /** Async slug uniqueness check — returns null if valid, error otherwise. */
  onValidateSlug?: (slug: string) => Promise<SlugValidationError | null>;
  /** Async port availability check — returns null if valid, error otherwise. */
  onValidatePort?: (
    port: number,
    field: "backend_port" | "frontend_port" | "db_port",
  ) => Promise<PortValidationError | null>;
  /** Disables submit and shows loading state. */
  loading?: boolean;
  /** Server-side error message displayed above the submit button. */
  error?: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GITHUB_ORG = "rauschiccsk";
const GITHUB_REPO_RE = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

/** Convert a human name to a URL-safe slug. */
function toSlug(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Build the default GitHub repo URL from a slug. */
function deriveRepo(slug: string): string {
  return slug ? `https://github.com/${GITHUB_ORG}/${slug}` : "";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function NewProjectForm({
  onSubmit,
  onValidateSlug,
  onValidatePort,
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

  // -- Derivation flags -----------------------------------------------------
  const [slugTouchedByUser, setSlugTouchedByUser] = useState(false);
  const [repoTouchedByUser, setRepoTouchedByUser] = useState(false);

  // -- Validation state -----------------------------------------------------
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [slugError, setSlugError] = useState<string | null>(null);
  const [repoError, setRepoError] = useState<string | null>(null);
  const [portErrors, setPortErrors] = useState<Record<string, string | null>>({
    backend_port: null,
    frontend_port: null,
    db_port: null,
  });

  // -- Refs for debounce timers ---------------------------------------------
  const slugTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const portTimerRefs = useRef<Record<string, ReturnType<typeof setTimeout> | null>>({
    backend_port: null,
    frontend_port: null,
    db_port: null,
  });

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
    if (!GITHUB_REPO_RE.test(githubRepo)) {
      setRepoError("Format: https://github.com/org/repo");
    } else {
      setRepoError(null);
    }
  }, [githubRepo]);

  // -- Debounced port validation -------------------------------------------
  const validatePort = useCallback(
    (value: string, field: "backend_port" | "frontend_port" | "db_port") => {
      if (portTimerRefs.current[field]) {
        clearTimeout(portTimerRefs.current[field]!);
      }

      const num = parseInt(value, 10);
      if (!value || isNaN(num)) {
        setPortErrors((prev) => ({ ...prev, [field]: null }));
        return;
      }

      if (num < 1 || num > 65535) {
        setPortErrors((prev) => ({ ...prev, [field]: "Port must be 1\u201365535" }));
        return;
      }

      if (!onValidatePort) {
        setPortErrors((prev) => ({ ...prev, [field]: null }));
        return;
      }

      portTimerRefs.current[field] = setTimeout(async () => {
        const result = await onValidatePort(num, field);
        setPortErrors((prev) => ({ ...prev, [field]: result?.message ?? null }));
      }, 400);
    },
    [onValidatePort],
  );

  // -- Client-side validation -----------------------------------------------
  const nameError = touched.name && name.trim() === "" ? "Name is required." : null;
  const slugEmpty = touched.slug && slug.trim() === "" ? "Slug is required." : null;

  const hasErrors =
    !!nameError ||
    !!slugEmpty ||
    !!slugError ||
    !!repoError ||
    !!portErrors.backend_port ||
    !!portErrors.frontend_port ||
    !!portErrors.db_port;

  // -- Submit ---------------------------------------------------------------
  function handleSubmit(e: FormEvent) {
    e.preventDefault();

    // Mark required fields as touched
    setTouched({ name: true, slug: true });

    if (name.trim() === "" || slug.trim() === "") return;
    if (hasErrors) return;

    const data: ProjectCreationFormData = {
      name: name.trim(),
      slug: slug.trim(),
      category,
      description: description.trim(),
      github_repo: githubRepo.trim(),
      backend_port: backendPort ? parseInt(backendPort, 10) : null,
      frontend_port: frontendPort ? parseInt(frontendPort, 10) : null,
      db_port: dbPort ? parseInt(dbPort, 10) : null,
    };

    onSubmit(data);
  }

  // -- Shared input class ---------------------------------------------------
  const inputClass = (hasErr: boolean) =>
    `w-full rounded-md border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 dark:bg-gray-800 dark:text-gray-100 ${
      hasErr
        ? "border-red-500 focus:ring-red-500"
        : "border-gray-300 focus:ring-primary-500 dark:border-gray-600"
    }`;

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      data-testid="new-project-form"
      className="space-y-5"
    >
      {/* Name */}
      <div>
        <label
          htmlFor="project-name"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Project Name *
        </label>
        <input
          id="project-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={handleNameBlur}
          disabled={loading}
          placeholder="NEX Horizont"
          className={inputClass(!!nameError)}
          data-testid="project-name"
        />
        {nameError && (
          <p className="mt-1 text-xs text-red-600" role="alert">
            {nameError}
          </p>
        )}
      </div>

      {/* Slug */}
      <div>
        <label
          htmlFor="project-slug"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
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
          placeholder="nex-horizont"
          className={inputClass(!!slugEmpty || !!slugError)}
          data-testid="project-slug"
        />
        {slugEmpty && (
          <p className="mt-1 text-xs text-red-600" role="alert">
            {slugEmpty}
          </p>
        )}
        {slugError && (
          <p className="mt-1 text-xs text-red-600" role="alert" data-testid="slug-error">
            {slugError}
          </p>
        )}
      </div>

      {/* GitHub Repo */}
      <div>
        <label
          htmlFor="project-repo"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          GitHub Repository
        </label>
        <input
          id="project-repo"
          type="text"
          value={githubRepo}
          onChange={(e) => handleRepoChange(e.target.value)}
          disabled={loading}
          placeholder={`https://github.com/${GITHUB_ORG}/your-project`}
          className={inputClass(!!repoError)}
          data-testid="project-repo"
        />
        {repoError && (
          <p className="mt-1 text-xs text-red-600" role="alert" data-testid="repo-error">
            {repoError}
          </p>
        )}
      </div>

      {/* Category */}
      <div>
        <label
          htmlFor="project-category"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Category
        </label>
        <select
          id="project-category"
          value={category}
          onChange={(e) => setCategory(e.target.value as ProjectCategory)}
          disabled={loading}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
          data-testid="project-category"
        >
          <option value="singlemodule">Single Module</option>
          <option value="multimodule">Multi Module</option>
        </select>
      </div>

      {/* Description */}
      <div>
        <label
          htmlFor="project-description"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Description
        </label>
        <textarea
          id="project-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={loading}
          rows={3}
          placeholder="Brief project description..."
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
          data-testid="project-description"
        />
      </div>

      {/* Ports — 3-column grid */}
      <fieldset>
        <legend className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-300">
          Port Assignment
        </legend>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {/* Backend port */}
          <div>
            <label
              htmlFor="backend-port"
              className="mb-1 block text-xs text-gray-500 dark:text-gray-400"
            >
              Backend Port
            </label>
            <input
              id="backend-port"
              type="number"
              min={1}
              max={65535}
              value={backendPort}
              onChange={(e) => {
                setBackendPort(e.target.value);
                validatePort(e.target.value, "backend_port");
              }}
              disabled={loading}
              placeholder="9176"
              className={inputClass(!!portErrors.backend_port)}
              data-testid="backend-port"
            />
            {portErrors.backend_port && (
              <p className="mt-1 text-xs text-red-600" role="alert">
                {portErrors.backend_port}
              </p>
            )}
          </div>

          {/* Frontend port */}
          <div>
            <label
              htmlFor="frontend-port"
              className="mb-1 block text-xs text-gray-500 dark:text-gray-400"
            >
              Frontend Port
            </label>
            <input
              id="frontend-port"
              type="number"
              min={1}
              max={65535}
              value={frontendPort}
              onChange={(e) => {
                setFrontendPort(e.target.value);
                validatePort(e.target.value, "frontend_port");
              }}
              disabled={loading}
              placeholder="9177"
              className={inputClass(!!portErrors.frontend_port)}
              data-testid="frontend-port"
            />
            {portErrors.frontend_port && (
              <p className="mt-1 text-xs text-red-600" role="alert">
                {portErrors.frontend_port}
              </p>
            )}
          </div>

          {/* DB port */}
          <div>
            <label
              htmlFor="db-port"
              className="mb-1 block text-xs text-gray-500 dark:text-gray-400"
            >
              Database Port
            </label>
            <input
              id="db-port"
              type="number"
              min={1}
              max={65535}
              value={dbPort}
              onChange={(e) => {
                setDbPort(e.target.value);
                validatePort(e.target.value, "db_port");
              }}
              disabled={loading}
              placeholder="5432"
              className={inputClass(!!portErrors.db_port)}
              data-testid="db-port"
            />
            {portErrors.db_port && (
              <p className="mt-1 text-xs text-red-600" role="alert">
                {portErrors.db_port}
              </p>
            )}
          </div>
        </div>
      </fieldset>

      {/* Server error banner */}
      {error && (
        <div
          className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-400"
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
        className="btn-primary w-full"
        data-testid="submit-button"
      >
        {loading ? "Vytvaram projekt\u2026" : "Vytvorit projekt"}
      </button>
    </form>
  );
}
