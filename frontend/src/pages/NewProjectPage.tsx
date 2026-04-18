/**
 * New Project page — single screen form for creating a new project.
 *
 * Wraps {@link NewProjectForm} with page-level concerns:
 *   - Navigation on successful creation
 *   - Server-side error handling
 *   - Slug and port validation callbacks (stubbed for now — wired in
 *     Tasks 24.3–24.6)
 */

import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import NewProjectForm from "@/components/projects/NewProjectForm";
import { api, ApiError } from "@/services/api";
import type { ProjectCreationFormData, ProjectRead } from "@/types";

function NewProjectPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (data: ProjectCreationFormData) => {
      setLoading(true);
      setError(null);

      try {
        const project = await api.post<ProjectRead>("/projects", {
          name: data.name,
          slug: data.slug,
          category: data.category,
          description: data.description,
          repo_url: data.github_repo || null,
          backend_port: data.backend_port,
          frontend_port: data.frontend_port,
          db_port: data.db_port,
        });

        navigate(`/projects/${project.slug}`, { replace: true });
      } catch (err) {
        if (err instanceof ApiError) {
          setError(
            typeof err.data === "string"
              ? err.data
              : err.message || "Failed to create project.",
          );
        } else {
          setError("Network error. Please check your connection.");
        }
      } finally {
        setLoading(false);
      }
    },
    [navigate],
  );

  return (
    <section className="mx-auto max-w-2xl space-y-6" data-testid="new-project-page">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => navigate("/projects")}
          className="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          aria-label="Back to projects"
          data-testid="back-button"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
          Novy projekt
        </h2>
      </div>

      {/* Form */}
      <div className="rounded-xl border border-gray-700 bg-gray-800 p-6 shadow-xl">
        <NewProjectForm onSubmit={handleSubmit} loading={loading} error={error} />
      </div>
    </section>
  );
}

export default NewProjectPage;
