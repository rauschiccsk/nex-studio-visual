// DEV-492, DEV-498 — the tickets put into a version, for its page (a fast fix starts from them; a regular version shows
// them before „Spustiť tvorbu špecifikácie“).
import { useEffect, useState } from "react";

import { listVersionTicketsApi, type VersionTicketItem } from "@/services/api/versions";
import { humanizeApiError, type HumanError } from "@/services/apiError";

export function useVersionTickets(versionId: string): { tickets: VersionTicketItem[] | null; error: HumanError | null } {
  const [tickets, setTickets] = useState<VersionTicketItem[] | null>(null);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let live = true;
    listVersionTicketsApi(versionId)
      .then((found) => live && setTickets(found))
      .catch((e: unknown) => live && setError(humanizeApiError(e, "Tikety verzie sa nepodarilo načítať")));
    return () => {
      live = false;
    };
  }, [versionId]);

  return { tickets, error };
}
