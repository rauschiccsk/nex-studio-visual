/**
 * Nepoužívaný kód z minulých verzií (DEV-156) — what the cockpit's check finds in a project that earlier versions left
 * behind, and one click that prepares its cleanup as a fast fix.
 *
 * Director 20.10.2026: „Chcem aby takáto kontrola bola zabudovaná do kokpitu.“ Verifikácia stops only the dead code a
 * version adds; the old one is offered here. Nothing starts by itself: the button opens the project's „Rýchla
 * oprava“ dialog with the prepared text (the page does that — it owns the dialog), where the Manažér reads it, picks
 * the work kind and starts it. The panel says nothing while the check runs and nothing when there is nothing to
 * clean; when the check could not run, it says why.
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";

import { getDeadCodeApi, type DeadCode } from "@/services/api/deadCode";

/** How many findings the panel lists before „Zobraziť všetky“. */
const SHOWN = 5;

interface Props {
  slug: string;
  /** Opens the project's fast-fix dialog with the prepared text — absent when he may not start a fast fix. */
  onCleanUp?: (brief: string) => void;
}

export default function DeadCodeCleanupPanel({ slug, onCleanUp }: Props) {
  const [found, setFound] = useState<DeadCode | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getDeadCodeApi(slug)
      .then((result) => !cancelled && setFound(result))
      .catch(() => !cancelled && setFound(null));
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (!found) return null;
  const count = found.findings.length;
  if (count === 0 && found.not_run.length === 0) return null;

  const listed = all ? found.findings : found.findings.slice(0, SHOWN);
  return (
    <section
      aria-label="Nepoužívaný kód z minulých verzií"
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Trash2 className="h-3.5 w-3.5" /> Nepoužívaný kód z minulých verzií
      </h2>
      {count > 0 && (
        <>
          <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
            Kontrola kokpitu našla v projekte {count}{" "}
            {count === 1 ? "vec" : count < 5 ? "veci" : "vecí"}, ktoré nikto
            nepoužíva. Nové verzie ďalší taký kód nepridajú — Verifikácia ich
            zastaví.
          </p>
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-[var(--color-text-primary)]">
            {listed.map((f) => (
              <li key={`${f.kind}:${f.path}:${f.name}`}>{f.sentence}</li>
            ))}
          </ul>
          {count > SHOWN && (
            <button
              type="button"
              onClick={() => setAll(!all)}
              className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
            >
              {all ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              {all ? "Zobraziť menej" : `Zobraziť všetky (${count})`}
            </button>
          )}
          {onCleanUp && found.brief && (
            <div className="mt-3">
              <button
                type="button"
                onClick={() => found.brief && onCleanUp(found.brief)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
              >
                Upratať nepoužívaný kód
              </button>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                Otvorí rýchlu opravu so zoznamom — prečítaš ju, vyberieš druh
                práce a spustíš sám.
              </p>
            </div>
          )}
        </>
      )}
      {found.not_run.map((reason) => (
        <p key={reason} className="mt-2 text-xs text-[var(--color-text-muted)]">
          Kontrola nepoužívaného kódu neprebehla celá: {reason}.
        </p>
      ))}
    </section>
  );
}
