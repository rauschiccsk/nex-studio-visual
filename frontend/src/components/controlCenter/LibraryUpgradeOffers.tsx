// The opt-in upgrades offered when a version is founded — the app template's nex-app-kit (DEV-253) first, nex-shared
// (#3) after it — in ONE place for every way a version is founded (DEV-379).
//
// 13.11.2026 NEX Manager 1.6.0 was founded from the brief the technical team prepared („Založiť novú verziu“ on the
// project's page) and stayed on nex-app-kit v0.11.0 — the version that was to bring v0.13.0. The offer lived only on
// the „Nová verzia“ page; the Staviteľ caught it in Príprava. Director, on the ticket and on doing it: „Súhlasím“.
//
// Shown only while the project's working tree is clean — an upgrade commits its pins, and unsaved work must be
// resolved first (the founding guard of v4.0.25); an upgrade that wrote its pin but could not save it says so.
import { useEffect, useRef } from "react";

import {
  getAppKitStatusApi,
  getNexsharedStatusApi,
  upgradeAppKitApi,
  upgradeNexsharedApi,
  type AppKitStatus,
  type NexsharedStatus,
} from "@/services/api/projects";
import { useLibraryUpgrade } from "@/hooks/useLibraryUpgrade";
import ErrorNote from "@/components/common/ErrorNote";
import { AppKitUpgradePrompt } from "@/components/controlCenter/AppKitUpgradePrompt";
import { NexsharedUpgradePrompt } from "@/components/controlCenter/NexsharedUpgradePrompt";

interface Props {
  projectId: string;
  /** The project is on an app template — only then is nex-app-kit in it to upgrade. */
  hasTemplate: boolean;
  /** The project's working tree is clean (known and clean) — the prompts wait until it is. */
  clean: boolean;
  /** Nothing to offer any more (the version was founded). */
  hidden?: boolean;
  /** Read both statuses again when it changes (after the project was put onto a template). */
  reloadKey?: number;
  /** Runs after every upgrade attempt — the caller re-reads the working tree its own guard judges. */
  afterwards: () => Promise<unknown>;
}

export default function LibraryUpgradeOffers({ projectId, hasTemplate, clean, hidden, reloadKey, afterwards }: Props) {
  const nex = useLibraryUpgrade<NexsharedStatus>(
    "nex-shared",
    (target) => upgradeNexsharedApi(projectId, target),
    afterwards,
  );
  const kit = useLibraryUpgrade<AppKitStatus>(
    "šablóny aplikácie",
    (target) => upgradeAppKitApi(projectId, target),
    afterwards,
  );
  // React keeps a state setter the same for the component's life — the load below may depend on it.
  const { setStatus: setNexStatus } = nex;
  const { setStatus: setKitStatus } = kit;

  useEffect(() => {
    let cancelled = false;
    // Best-effort — a failure only skips the prompt.
    getNexsharedStatusApi(projectId)
      .then((st) => { if (!cancelled) setNexStatus(st); })
      .catch(() => {});
    if (hasTemplate) {
      getAppKitStatusApi(projectId)
        .then((st) => { if (!cancelled) setKitStatus(st); })
        .catch(() => {});
    }
    return () => { cancelled = true; };
  }, [projectId, hasTemplate, reloadKey, setNexStatus, setKitStatus]);

  // A tree that turns clean (the unsaved work saved or thrown away) settles a pin that was written but not saved —
  // leaving that note up would be the same lie in the other direction.
  const wasClean = useRef(clean);
  const { clearError: clearNexError } = nex;
  const { clearError: clearKitError } = kit;
  useEffect(() => {
    if (clean && !wasClean.current) {
      clearNexError();
      clearKitError();
    }
    wasClean.current = clean;
  }, [clean, clearNexError, clearKitError]);

  if (hidden) return null;
  return (
    <>
      {clean && kit.offered && kit.status && (
        <div className="mb-5">
          <AppKitUpgradePrompt
            status={kit.status}
            busy={kit.busy}
            onUpgrade={(target) => void kit.run(target)}
            onStay={kit.dismiss}
          />
        </div>
      )}
      {clean && nex.offered && nex.status && (
        <div className="mb-5">
          <NexsharedUpgradePrompt
            status={nex.status}
            busy={nex.busy}
            onUpgrade={(target) => void nex.run(target)}
            onStay={nex.dismiss}
          />
        </div>
      )}
      {/* An upgrade that wrote the pin but could not save it leaves the tree dirty — which hides the prompt above —
          so its failure has to stand on its own line. */}
      {[kit.error, nex.error].map((error, i) => (
        <ErrorNote
          key={i}
          error={error}
          className="mb-5 rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-3"
        />
      ))}
    </>
  );
}
