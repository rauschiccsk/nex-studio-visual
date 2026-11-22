// FastFixTickets — the page of a fast fix that has not started (DEV-492): the tickets put into it and „Spustiť rýchlu
// opravu“.
//
// One way for every version (Director 22.11.2026: „Treba mať jednotný postup a to najprv verzia a potom priradzovanie
// tiketov.“): a fast fix is founded first, its tickets are put in on the „Plane tikety“ tab, and it starts here. Its
// brief is the text of those tickets — it has no Zadanie of its own, and without a ticket it does not start.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import VersionTicketList from "@/components/version/VersionTicketList";
import { useVersionTickets } from "@/hooks/useVersionTickets";
import { postPipelineActionApi } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { Version } from "@/types/version";

interface Props {
  slug: string;
  version: Version;
  canStart: boolean;
}

/** Why „Spustiť rýchlu opravu“ waits — said on the disabled button and under the empty list. */
const NO_TICKET_YET =
  "Rýchla oprava ešte nemá zaradený žiadny tiket — zaraď ho na karte „Plane tikety“ tlačidlom „Zaradiť do verzie“.";

export default function FastFixTickets({ slug, version, canStart }: Props) {
  const navigate = useNavigate();
  const { tickets, error } = useVersionTickets(version.id);
  const [startError, setStartError] = useState<HumanError | null>(null);
  const [starting, setStarting] = useState(false);

  async function start() {
    if (!tickets?.length || starting) return;
    setStarting(true);
    setStartError(null);
    try {
      await postPipelineActionApi(version.id, { action: "start" });
      navigate("/riadiace-centrum");
    } catch (e: unknown) {
      setStartError(humanizeApiError(e, "Spustenie rýchlej opravy zlyhalo"));
      setStarting(false);
    }
  }

  const empty = tickets !== null && tickets.length === 0;
  return (
    <section
      aria-label="Tikety rýchlej opravy"
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5"
    >
      <div className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">
        Rýchla oprava {version.version_number} — tikety
      </div>
      <p className="mb-3 text-xs text-[var(--color-text-muted)]">
        Zadaním rýchlej opravy sú tikety, ktoré do nej zaradíš. Po spustení ich Staviteľ dostane celé a po nasadení do
        PROD ich kokpit uzavrie.
      </p>
      <ErrorNote error={startError} className="mb-2" />
      <VersionTicketList slug={slug} tickets={tickets} error={error} empty={NO_TICKET_YET} emptyTone="warning">
        {canStart && (
          <button
            type="button"
            onClick={start}
            disabled={!tickets?.length || starting}
            title={empty ? NO_TICKET_YET : undefined}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
          >
            <Zap className="h-3.5 w-3.5" aria-hidden="true" />
            {starting ? "Spúšťam…" : "Spustiť rýchlu opravu"}
          </button>
        )}
      </VersionTicketList>
    </section>
  );
}
