// DeployBlockNotice — WHY the Nasadiť button is closed, said out loud (v4.0.54).
//
// "Overená verzia" is recomputed against live git on every read, so a version drops out of the deployable
// list the moment the project's code moves past the commit it was checked at. Until now the UAT/PROD screen
// greyed Nasadiť out in SILENCE: the only reason it produced went into a native `title` on a disabled button
// (which browsers suppress), the only visible text was a muted "žiadna overená verzia", and the sub-label
// that could have explained it was gated to the PROD tab. A Junior hit exactly this on UAT and had to be
// unblocked from a terminal (incident 2026-07-26, nex-websites) — a self-sufficiency gap, not a UI nicety.
//
// This notice closes both halves: (1) LEGIBILITY — plain-Slovak cause, naming the affected version;
// (2) ACTIONABILITY — the recovery on THIS screen, where the dead end is.
//
// Honest-by-construction (mirrors ReverifyBar / SchvalitBar): renders NOTHING when the backend says `ok`,
// and offers "Over znova" ONLY when the backend says this user may run it right now (`can_reverify`, which
// is true solely for the `drift` cause — its handler rejects every other shape). Where there is no action
// this user can take, no button is drawn: a button that 400s or 403s would just move the dead end one click.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CircleAlert, FolderOpen, Loader2, RotateCw } from "lucide-react";

import WarningActionBar from "@/components/common/WarningActionBar";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { DeployBlock } from "@/types/deploy";
import { fmtVer } from "./version";

export interface DeployBlockNoticeProps {
  /** Optional so a response from an older backend (mid-redeploy skew) renders nothing instead of throwing. */
  block: DeployBlock | null | undefined;
  /** Project slug — used to send the manager to the affected version (which also pins it). */
  projectSlug: string;
  /** Called once a re-verification was successfully started, so the page reloads + starts polling. */
  onReverifyStarted: () => void;
}

export default function DeployBlockNotice({ block, projectSlug, onReverifyStarted }: DeployBlockNoticeProps) {
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  if (!block || block.cause === "ok") return null;

  const version = fmtVer(block.version_number);

  async function reverify() {
    const versionId = block?.version_id;
    if (!versionId) return;
    setError(null);
    setSubmitting(true);
    try {
      await postPipelineActionApi(versionId, { action: "overit_znovu" });
      onReverifyStarted();
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Opätovné overenie sa nepodarilo spustiť"));
    } finally {
      setSubmitting(false);
    }
  }

  const openVersion = block.version_id
    ? {
        label: "Otvoriť verziu",
        icon: "open" as const,
        run: () => navigate(`/projects/${projectSlug}/versions/${block.version_id}`),
      }
    : null;

  // The copy is per CAUSE, because the remedies genuinely differ — one sentence for all of them would send
  // the manager to the wrong place. No jargon: never "commit", "HEAD", "SHA", "drift", "overená verzia".
  let title: string;
  let body: string;
  let action: { label: string; icon: "reverify" | "open"; run: () => void } | null = null;

  switch (block.cause) {
    case "drift":
      title = "Nasadenie je pozastavené — verziu treba znova overiť";
      body =
        `Verziu ${version} sme označili ako hotovú, ale v projekte odvtedy pribudli ďalšie úpravy. ` +
        "To, čo by sa teraz nasadilo, už nie je presne to, čo sme vtedy vyskúšali — preto to zatiaľ " +
        "nepúšťame k zákazníkovi." +
        (block.can_reverify
          ? " Klikni na „Over znova“: aplikácia sa nanovo spustí a prekontroluje. Ak bude všetko v poriadku, " +
            "verzia sa sama označí ako hotová a nasadenie sa tu odomkne. Zvyčajne to trvá pár minút."
          : " Znova overiť ju môže vlastník projektu alebo Manažér — popros o to niekoho z nich.");
      if (block.can_reverify) {
        action = { label: submitting ? "Spúšťam…" : "Over znova", icon: "reverify", run: reverify };
      }
      break;

    case "reverify_running":
      title = `Overujem verziu ${version}…`;
      body =
        "Aplikácia sa práve spúšťa a kontroluje. Pokojne rob medzitým niečo iné — keď bude hotovo, " +
        "nasadenie sa tu odomkne samo. Táto stránka sa obnovuje sama, nemusíš nič klikať.";
      break;

    case "version_busy":
      // Drifted, but the pipeline is mid-work or stuck: re-verify would be rejected, and this is NOT the
      // self-unlocking run — so no button and, deliberately, no promise that it resolves on its own.
      title = `Na verzii ${version} sa práve pracuje`;
      body =
        "Verzia čaká na dokončenie rozrobenej práce, preto ju teraz nejde nasadiť. Otvor verziu a pozri sa, " +
        "v akom je stave — keď bude práca dokončená, vráť sa sem.";
      action = openVersion;
      break;

    case "awaiting_signoff":
      title = "Verzia je prekontrolovaná — chýba už len tvoje schválenie";
      body =
        `Kontrola verzie ${version} dopadla dobre. Aby sa dala nasadiť, treba ju ešte schváliť ako hotovú. ` +
        "Otvor verziu a potvrď ju — potom sa nasadenie tu odomkne.";
      action = openVersion;
      break;

    case "stale_signoff":
      title = "Verziu treba nanovo prekontrolovať a označiť ako hotovú";
      body =
        `Po tom, čo bola verzia ${version} označená ako hotová, na nej ešte prebehli ďalšie práce. ` +
        "Preto ju treba znova prekontrolovať a nanovo označiť ako hotovú — až potom sa dá nasadiť.";
      action = openVersion;
      break;

    case "none_finished":
      title = "Zatiaľ nie je čo nasadiť";
      body =
        "V tomto projekte zatiaľ nie je hotová žiadna verzia. Nasadiť sa dá až verzia označená ako hotová. " +
        "Otvor projekt a dokonči rozpracovanú verziu.";
      action = { label: "Otvoriť projekt", icon: "open", run: () => navigate(`/projects/${projectSlug}`) };
      break;

    default:
      // An unknown cause (a newer backend than this bundle) — say NOTHING rather than assert something
      // false. Silence is the old behaviour; a confidently wrong claim would be worse than the bug we fixed.
      return null;
  }

  const running = block.cause === "reverify_running";
  const isReverify = action?.icon === "reverify";

  return (
    // Chrome shared with ReverifyBar (WarningActionBar) — the two ends of the drift flow, one click apart.
    <WarningActionBar
      variant="card"
      title={title}
      icon={running ? Loader2 : CircleAlert}
      iconSpinning={running}
      action={
        action
          ? {
              label: action.label,
              icon: isReverify ? RotateCw : FolderOpen,
              spinning: isReverify && submitting,
              disabled: submitting,
              onClick: action.run,
            }
          : null
      }
      error={error}
    >
      {body}
    </WarningActionBar>
  );
}
