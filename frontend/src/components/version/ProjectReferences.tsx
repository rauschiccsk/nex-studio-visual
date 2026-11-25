// ProjectReferences — the documents from the internet the cockpit downloaded into the project (DEV-511), right where
// the Manažér starts the Špecifikácia: what came, what did not and why, and „Stiahnuť znova“. 25.11.2026 the brief of
// NEX DualBook left ten such documents to the Manažér to download by hand — the Director: „…mám ich vložiť ručne čo ja
// robiť nebudem.“ Nothing shows for a project whose brief listed none.
import { useCallback, useEffect, useState } from "react";

import ErrorNote from "@/components/common/ErrorNote";
import { getReferenceDocsApi, retryReferenceDocsApi, type ReferenceDocs } from "@/services/api/projects";
import { humanizeApiError, type HumanError } from "@/services/apiError";

interface Props {
  projectId: string;
  canRetry: boolean;
}

/** How often the list is asked again while a download goes on. */
const WHILE_RUNNING_MS = 3000;

function sizeOf(bytes: number | null | undefined): string {
  if (bytes == null) return "";
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

export default function ProjectReferences({ projectId, canRetry }: Props) {
  const [docs, setDocs] = useState<ReferenceDocs | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  const [retrying, setRetrying] = useState(false);

  const load = useCallback(async () => {
    try {
      setDocs(await getReferenceDocsApi(projectId));
      setError(null);
    } catch (e) {
      setError(humanizeApiError(e, "Podklady z internetu sa nepodarilo načítať"));
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = docs?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), WHILE_RUNNING_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  if (!docs || (docs.items.length === 0 && !docs.running)) {
    return error ? <ErrorNote error={error} className="mt-2" /> : null;
  }

  const missing = docs.items.filter((one) => !one.ok).length;

  async function retry() {
    setRetrying(true);
    try {
      setDocs(await retryReferenceDocsApi(projectId));
      setError(null);
    } catch (e) {
      setError(humanizeApiError(e, "Podklady sa nepodarilo stiahnuť znova"));
    } finally {
      setRetrying(false);
    }
  }

  return (
    <section aria-label="Podklady z internetu" className="mt-4 border-t border-[var(--color-border-default)] pt-3">
      <div className="mb-1 text-xs font-semibold text-[var(--color-text-primary)]">Podklady z internetu</div>
      <p className="mb-2 text-xs text-[var(--color-text-muted)]">
        Kokpit ich stiahol podľa zoznamu v zadaní do priečinka docs/reference/ projektu — Staviteľ ich tam číta bez
        internetu.
      </p>
      {running && <p className="mb-2 text-xs text-[var(--color-text-secondary)]">Sťahujem podklady…</p>}
      <ul className="space-y-1 text-xs">
        {docs.items.map((one) => (
          <li
            key={one.path}
            className={one.ok ? "text-[var(--color-text-secondary)]" : "text-[var(--color-state-error-fg)]"}
          >
            {one.ok ? "✓ " : "✗ "}
            <a href={one.url} target="_blank" rel="noreferrer" className="underline">
              {one.description}
            </a>{" "}
            <span className="font-mono text-[var(--color-text-muted)]">{one.path}</span>
            {one.ok ? ` (${sizeOf(one.size)})` : ` — nestiahnuté: ${one.said ?? ""}`}
          </li>
        ))}
      </ul>
      {missing > 0 && canRetry && (
        <button
          type="button"
          onClick={() => void retry()}
          disabled={retrying || running}
          title={running ? "Sťahovanie práve beží." : undefined}
          className="mt-2 rounded-md border border-[var(--color-border-strong)] px-2 py-1 text-xs text-[var(--color-text-primary)] disabled:opacity-50"
        >
          Stiahnuť znova
        </button>
      )}
      <ErrorNote error={error} className="mt-2" />
    </section>
  );
}
