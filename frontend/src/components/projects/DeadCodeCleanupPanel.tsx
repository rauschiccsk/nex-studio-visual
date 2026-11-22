/**
 * Nepoužívaný kód z minulých verzií (DEV-156) — what the cockpit's check finds in a project that earlier versions left
 * behind, and one click that prepares its cleanup as a ticket of the project (DEV-492).
 *
 * Director 20.10.2026: „Chcem aby takáto kontrola bola zabudovaná do kokpitu.“ Verifikácia stops only the dead code a
 * version adds; the old one is offered here. Nothing starts by itself: the button opens „Založiť tiket“ with the
 * prepared text, where the Manažér reads it, picks the work kind and files it — then puts it into a version or a fast
 * fix like any ticket. The panel says nothing while the check runs and nothing when there is nothing to clean; when the
 * check could not run, it says why.
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";

import { getDeadCodeApi, type DeadCode } from "@/services/api/deadCode";
import PreparedTicketDialog from "@/components/planeTickets/PreparedTicketDialog";

/** How many findings the panel lists before „Zobraziť všetky“. */
const SHOWN = 5;

interface Props {
  slug: string;
  /** Whether he may file a ticket of the project — the button is not there otherwise. */
  canFile: boolean;
}

/** The ticket's name — the text is the check's own brief. */
const DEAD_CODE_TICKET_TITLE = "Upratať nepoužívaný kód z minulých verzií";

export default function DeadCodeCleanupPanel({ slug, canFile }: Props) {
  const [found, setFound] = useState<DeadCode | null>(null);
  const [all, setAll] = useState(false);
  const [filing, setFiling] = useState(false);

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
          {canFile && found.brief && (
            <div className="mt-3">
              <button
                type="button"
                onClick={() => setFiling(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
              >
                Upratať nepoužívaný kód
              </button>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                Pripraví tiket so zoznamom — prečítaš ho, vyberieš druh práce a
                založíš; potom ho zaradíš do verzie alebo do rýchlej opravy.
              </p>
            </div>
          )}
          {filing && found.brief && (
            <PreparedTicketDialog
              slug={slug}
              prepared={{
                title: DEAD_CODE_TICKET_TITLE,
                description: found.brief,
                source: "dead_code",
                workKind: null,
              }}
              onClose={() => setFiling(false)}
            />
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
