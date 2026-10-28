import { useState } from "react";
import { humanizeApiError, type HumanError } from "@/services/apiError";

/**
 * One kit's opt-in upgrade when a version is founded (#3 nex-shared, DEV-253 nex-app-kit): the status the prompt
 * shows, the click, and what the click left behind. *name* is the kit as a sentence says it („nex-shared“, „šablóny
 * aplikácie“); *afterwards* runs after every attempt — an upgrade can leave an uncommitted pin, and the founding guard
 * must judge the tree as it is now.
 */
export function useLibraryUpgrade<S extends { current?: string | null; behind?: number }>(
  name: string,
  upgrade: (target: string) => Promise<{ committed: boolean }>,
  afterwards: () => Promise<unknown>,
) {
  const [status, setStatus] = useState<S | null>(null);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  async function run(target: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await upgrade(target);
      if (res.committed) {
        // Reflect the bump locally — nothing is behind any more, so the offer goes (the new version builds on it).
        setStatus((s) => (s ? { ...s, current: target, behind: 0, up_to_date: true, changelog: [] } : s));
      } else {
        // The pin WAS rewritten on disk but could not be committed — the project now carries an uncommitted change.
        // Reporting "up to date" here was the lie: keep the prompt live and name what actually happened, so the dirty
        // tree below is not a mystery.
        setError({
          message:
            `Povýšenie na v${target} sa zapísalo do súborov projektu, ale nepodarilo sa ho uložiť — ` +
            "projekt má teraz neuložené zmeny. Vyrieš ich v karte s neuloženými zmenami a skús to znova.",
        });
      }
    } catch (err: unknown) {
      setError(humanizeApiError(err, `Povýšenie ${name} na v${target} zlyhalo`));
    } finally {
      setBusy(false);
      await afterwards();
    }
  }

  const offered = status !== null && (status.behind ?? 0) > 0 && !dismissed;
  return {
    status,
    setStatus,
    busy,
    error,
    offered,
    run,
    dismiss: () => setDismissed(true),
    /** A clean tree settles a pin that was written but not saved — the note about it no longer applies. */
    clearError: () => setError(null),
  };
}
