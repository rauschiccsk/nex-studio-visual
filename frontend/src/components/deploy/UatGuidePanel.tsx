// DEV-90 — what to try in UAT, under the UAT table.
//
// 12.10.2026 the Director:
// „V záložke UAT by som privýtal súhrn toho čo mám otestovať v novej verzii.“
// The agent writes the version's test guide while it builds and the verification stage checks it; this panel
// shows the guide of the verified commit — or, for a version from before the guide, the changes its release note
// lists — with each customer's UAT link and the login of its first admin.
import { useEffect, useState } from "react";
import { ExternalLink, ListChecks } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { getUatGuide } from "@/services/api/deploy";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { DeployMatrixRow, UatGuide } from "@/types/deploy";

interface Props {
  slug: string;
  /** Verified versions, newest first — the ones UAT can get. */
  versions: string[];
  rows: DeployMatrixRow[];
}

export default function UatGuidePanel({ slug, versions, rows }: Props) {
  const [version, setVersion] = useState<string>(versions[0] ?? "");
  const [guide, setGuide] = useState<UatGuide | null>(null);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    if (!versions.includes(version)) setVersion(versions[0] ?? "");
  }, [versions, version]);

  useEffect(() => {
    if (!version) return;
    let live = true;
    setGuide(null);
    setError(null);
    getUatGuide(slug, version)
      .then((g) => live && setGuide(g))
      .catch((e) => live && setError(humanizeApiError(e, "Návod na skúšku sa nepodarilo načítať")));
    return () => {
      live = false;
    };
  }, [slug, version]);

  if (!versions.length) return null;
  const login = new Map((guide?.logins ?? []).map((l) => [l.customer_id, l.login]));
  const sections = guide?.sections ?? [];

  return (
    <section aria-labelledby="uat-guide-heading" className="mt-6 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <ListChecks className="h-4 w-4 text-[var(--color-text-secondary)]" aria-hidden />
        <h2 id="uat-guide-heading" className="text-sm font-semibold text-[var(--color-text-primary)]">
          Čo otestovať vo verzii
        </h2>
        <select
          aria-label="Verzia, ktorú skúšaš"
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          className="rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
        >
          {versions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>

      {rows.some((r) => r.uat_url) && (
        <ul className="mt-3 space-y-1 text-xs text-[var(--color-text-secondary)]">
          {rows
            .filter((r) => r.uat_url)
            .map((r) => (
              <li key={r.customer_id} className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium text-[var(--color-text-primary)]">{r.customer_name}:</span>
                <a href={r.uat_url ?? undefined} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary-400 hover:text-primary-300 pointer-coarse:min-h-11 pointer-coarse:min-w-11">
                  <ExternalLink className="h-3 w-3" aria-hidden /> UAT
                </a>
                {login.get(r.customer_id) && <span>prihlásenie: {login.get(r.customer_id)}</span>}
              </li>
            ))}
        </ul>
      )}

      {error && <ErrorNote error={error} className="mt-3" />}
      {!guide && !error && <p className="mt-3 text-xs text-[var(--color-text-muted)]">Načítavam návod…</p>}

      {guide?.source === "guide" && guide.nothing_to_test && sections.length === 0 && (
        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">Nie je čo skúšať: {guide.nothing_to_test}</p>
      )}
      {guide?.source === "guide" && sections.length > 0 && (
        <ol className="mt-3 space-y-3">
          {sections.map((s, i) => (
            <li key={i} className="rounded border border-[var(--color-border-default)] p-3">
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{s.title}</h3>
              {(s.screen || s.data) && (
                <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                  {s.screen && <>Obrazovka: {s.screen}</>}
                  {s.screen && s.data && " · "}
                  {s.data && <>Testovacie údaje: {s.data}</>}
                </p>
              )}
              <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-sm text-[var(--color-text-primary)]">
                {(s.steps ?? []).map((step, j) => (
                  <li key={j}>{step}</li>
                ))}
              </ol>
              {(s.expected ?? []).map((e, j) => (
                <p key={j} className="mt-2 text-sm text-[var(--color-state-success-fg)]">
                  Očakávané: {e}
                </p>
              ))}
            </li>
          ))}
        </ol>
      )}
      {guide?.source === "release_notes" && (
        <div className="mt-3">
          <p className="text-xs text-[var(--color-text-muted)]">
            Návod na skúšku pre túto verziu nevznikol — toto sú zmeny z jej poznámok k vydaniu:
          </p>
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-[var(--color-text-primary)]">
            {(guide.changes ?? []).map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {guide?.source === "none" && (
        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
          Pre túto verziu nie je čo ukázať — nemá návod na skúšku ani poznámky k vydaniu.
        </p>
      )}
    </section>
  );
}
