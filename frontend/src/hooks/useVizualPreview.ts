// useVizualPreview — DEV-127: the live preview of the Vizuál step as it really is, and one click to start it again.
//
// Director 15.10.2026: „ponúka schváliť vizuál … Ale URL na vizual nefunguje.“ The preview of NEX Manager 1.3.0 had
// died seven minutes after „Vizuál je pripravený" and the cockpit never noticed. Asked only in the Vizuál step,
// again every 20 s — a preview can die while the Manažér walks it.

import { useCallback, useEffect, useState } from "react";

import { getVizualPreviewApi, restartVizualPreviewApi, type VizualPreview } from "@/services/api/pipeline";
import { humanizeApiError } from "@/services/apiError";

const EVERY_MS = 20_000;
/** DEV-447: while the preview prepares its libraries it is asked more often, so the link comes as soon as it answers. */
const STARTING_EVERY_MS = 3_000;
/** DEV-447: after this long „pripravuje sa“ the strip says it takes longer than usual and offers to start it again. */
const STARTING_TOO_LONG_MS = 180_000;

export interface VizualPreviewState {
  preview: VizualPreview | null;
  restarting: boolean;
  error: string;
  restart: () => void;
  /** DEV-447: the preview has been preparing for longer than usual. */
  startingTooLong: boolean;
}

export function useVizualPreview(versionId: string | null, stage: string | null | undefined): VizualPreviewState {
  const [preview, setPreview] = useState<VizualPreview | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState("");
  const [startingSince, setStartingSince] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const active = !!versionId && stage === "vizual";
  const starting = preview?.needed === true && preview.state === "starting";

  useEffect(() => {
    if (!active) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    const ask = () =>
      getVizualPreviewApi(versionId!)
        .then((p) => {
          if (!cancelled) setPreview(p);
        })
        .catch(() => {
          /* the strip keeps the last known state; a failed check is not a dead preview */
        });
    void ask();
    const timer = window.setInterval(ask, starting ? STARTING_EVERY_MS : EVERY_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, versionId, starting]);

  // DEV-447: since when the preview prepares — the clock runs only while it does.
  useEffect(() => {
    if (!starting) {
      setStartingSince(null);
      return;
    }
    setStartingSince((since) => since ?? Date.now());
    const tick = window.setInterval(() => setNow(Date.now()), STARTING_EVERY_MS);
    return () => window.clearInterval(tick);
  }, [starting]);

  const restart = useCallback(() => {
    if (!versionId) return;
    setRestarting(true);
    setError("");
    restartVizualPreviewApi(versionId)
      .then(setPreview)
      .catch((e: unknown) => setError(humanizeApiError(e, "Spustenie náhľadu zlyhalo").message))
      .finally(() => setRestarting(false));
  }, [versionId]);

  const startingTooLong = startingSince !== null && now - startingSince >= STARTING_TOO_LONG_MS;
  return { preview, restarting, error, restart, startingTooLong };
}
