// PlannedVersionTickets — on the page of a regular version that has not started, the tickets put into it, right above
// „Spustiť tvorbu špecifikácie“ (DEV-498). Director 22.11.2026: „…v okne, kde spúšťam tvorbu špecifikácie, tiež nech sú
// zobrazené priradené tikety.“ The Staviteľ gets them in Príprava with the Zadanie — the Manažér writes no numbers.
import VersionTicketList from "@/components/version/VersionTicketList";
import { useVersionTickets } from "@/hooks/useVersionTickets";

interface Props {
  slug: string;
  versionId: string;
}

const NONE =
  "Do verzie zatiaľ nie je zaradený žiadny tiket — zaradíš ho na karte „Plane tikety“ tlačidlom „Zaradiť do verzie“.";

export default function PlannedVersionTickets({ slug, versionId }: Props) {
  const { tickets, error } = useVersionTickets(versionId);

  return (
    <section aria-label="Tikety verzie" className="mt-4 border-t border-[var(--color-border-default)] pt-3">
      <div className="mb-1 text-xs font-semibold text-[var(--color-text-primary)]">Tikety verzie</div>
      <p className="mb-2 text-xs text-[var(--color-text-muted)]">
        Staviteľ ich dostane v Príprave spolu so Zadaním; po nasadení do PROD ich kokpit uzavrie.
      </p>
      <VersionTicketList slug={slug} tickets={tickets} error={error} empty={NONE} emptyTone="muted" />
    </section>
  );
}
