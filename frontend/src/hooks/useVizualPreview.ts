// useVizualPreview — DEV-127: the live preview of the Vizuál step as it really is, and one click to start it again.
//
// Director 15.10.2026: „ponúka schváliť vizuál … Ale URL na vizual nefunguje.“ The preview of NEX Manager 1.3.0 had
// died seven minutes after „Vizuál je pripravený" and the cockpit never noticed. Asked only in the Vizuál step,
// again every 20 s — a preview can die while the Manažér walks it.

import { useCallback, useEffect, useState } from "react";

import { getVizualPreviewApi, restartVizualPreviewApi, type VizualPreview } from "@/services/api/pipeline";
import { humanizeApiError } from "@/services/apiError";

const EVERY_MS = 20_000;

export interface VizualPreviewState {
  preview: VizualPreview | null;
  restarting: boolean;
  error: string;
  restart: () => void;
}

export function useVizualPreview(versionId: string | null, stage: string | null | undefined): VizualPreviewState {
  const [preview, setPreview] = useState<VizualPreview | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState("");
  const active = !!versionId && stage === "vizual";

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
    const timer = window.setInterval(ask, EVERY_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, versionId]);

  const restart = useCallback(() => {
    if (!versionId) return;
    setRestarting(true);
    setError("");
    restartVizualPreviewApi(versionId)
      .then(setPreview)
      .catch((e: unknown) => setError(humanizeApiError(e, "Spustenie náhľadu zlyhalo").message))
      .finally(() => setRestarting(false));
  }, [versionId]);

  return { preview, restarting, error, restart };
}
