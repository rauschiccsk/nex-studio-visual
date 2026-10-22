/**
 * DEV-150 — „Kde stojíme“ on the Prehľad: where every project's development stands and how to carry on.
 *
 * Director 20.10.2026: „V NSV v záložke prehľad by som privítal informciu o tom, ako stojíme s daným projektom. Aby v
 * prípade, že vývoj projektu z nejakej príčiny nedokončíme (príde niečo dôležitejšie) mali by sme presnú informáciu o
 * tom, kde a ako pokračovať v projekte.“ The backend gives the cockpit's own records only (never the agent's text);
 * the sentences are worded here with the labels Riadiace centrum uses, and „Pokračovať“ opens the version there.
 */
import { useEffect, useState } from "react";

import ErrorNote from "@/components/common/ErrorNote";
import { useOpenVersionCockpit } from "@/hooks/useOpenVersionCockpit";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getStandingApi, type ProjectStanding } from "@/services/api/standing";

import { nextWords, prodWords, turnWords, versionWords } from "./standingWords";

function ProjectRow({ project }: { project: ProjectStanding }) {
  const openVersion = useOpenVersionCockpit();
  if (project.open_versions.length === 0) {
    return (
      <li className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-3">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">{project.name}</p>
        <p className="text-xs text-[var(--color-text-muted)]">Nič nestojí. {prodWords(project)}</p>
      </li>
    );
  }
  return (
    <>
      {project.open_versions.map((v) => (
        <li
          key={v.version_id}
          className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-3 flex items-start justify-between gap-3"
        >
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[var(--color-text-primary)]">{versionWords(project, v)}</p>
            <p className="text-xs text-[var(--color-text-secondary)]">{turnWords(v)}</p>
            <p className="text-xs text-[var(--color-text-muted)]">
              {nextWords(v)} {prodWords(project)}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void openVersion(v.version_id, { slug: project.slug, name: project.name })}
            className="shrink-0 px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors"
          >
            Pokračovať
          </button>
        </li>
      ))}
    </>
  );
}

export default function WhereWeStand() {
  const [projects, setProjects] = useState<ProjectStanding[] | null>(null);
  const [loadError, setLoadError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStandingApi()
      .then((res) => {
        if (!cancelled) setProjects(res.projects);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(humanizeApiError(err, "Stav projektov sa nepodarilo načítať"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="mb-8" aria-label="Kde stojíme">
      <h2 className="mb-3 text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-widest">Kde stojíme</h2>
      {loadError ? (
        // A failed load is not „nothing stands“ — the Prehľad must not claim a state it did not measure.
        <ErrorNote error={loadError} />
      ) : projects === null ? (
        <p className="text-sm text-[var(--color-text-muted)]">Načítavam…</p>
      ) : projects.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)]">Žiadny projekt, na ktorom pracuješ.</p>
      ) : (
        <ul className="space-y-2">
          {projects.map((p) => (
            <ProjectRow key={p.project_id} project={p} />
          ))}
        </ul>
      )}
    </section>
  );
}
