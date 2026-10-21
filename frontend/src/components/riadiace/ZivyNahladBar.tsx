// ZivyNahladBar — the live preview of the app during the Vizuál step, opened in its own window (DEV-120), as it
// really is (DEV-127).
//
// Director 15.10.2026: „Radšej odstráňme záložku "Vizuál". Aj tak nemôžem používať, lebo ak otvorím tu vizuál potom
// nevidím návod čo treba otestovať. Preto vizuál ja púšťam v Chrome a tak viem s tým pracovať.“ The Vizuál page
// left the sidebar; the one thing only it had — the preview's address with „Otvoriť vo vlastnom okne“ — stands here,
// beside the conversation that says what to check. Shown only in the Vizuál step: the sandbox is torn down when
// the step advances, so an address left over from a past run never reads as live.
//
// DEV-127 — the same day: „Ale URL na vizual nefunguje. Vypisuje "404 page not found"“. The preview of NEX Manager
// 1.3.0 had died seven minutes after „Vizuál je pripravený“ and the cockpit never noticed. The strip now says
// whether the preview runs, why it does not, and starts it again with one click.

import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import type { PipelineBoard, VizualPreview } from "@/services/api/pipeline";

interface Props {
  board: PipelineBoard | null;
  /** The preview's real state (useVizualPreview); null until first asked. */
  preview?: VizualPreview | null;
  restarting?: boolean;
  error?: string;
  onRestart?: () => void;
}

const STRIP =
  "flex flex-wrap items-center gap-2 border-b border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)]";
const LINK =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1 font-medium transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50 pointer-coarse:min-h-11";
const BUTTON = `ml-auto ${LINK}`;

export default function ZivyNahladBar({ board, preview = null, restarting = false, error = "", onRestart }: Props) {
  if (board?.state?.current_stage !== "vizual") return null;
  const down = preview?.needed && (preview.state === "stopped" || preview.state === "missing");
  if (down) {
    return (
      <div role="status" aria-label="Živý náhľad" className={STRIP}>
        <span className="text-[var(--color-state-warning-fg)]">
          Živý náhľad nebeží{preview.reason ? ` — ${preview.reason}` : "."}
        </span>
        <button type="button" onClick={onRestart} disabled={restarting || !onRestart} className={BUTTON}>
          {restarting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          {restarting ? "Spúšťam náhľad…" : "Spustiť náhľad znova"}
        </button>
        {error && <span className="w-full text-[var(--color-status-error)]">{error}</span>}
      </div>
    );
  }
  const url = (preview?.state === "running" ? preview.url : null) ?? board.vizual_url ?? null;
  // DEV-162: a Web project has a preview for each frontend — one link each, named the way the Manažér knows them.
  const frontends = preview?.state === "running" ? (preview.frontends ?? []) : [];
  if (frontends.length > 1) {
    return (
      <div role="status" aria-label="Živý náhľad" className={STRIP}>
        <span>Živé náhľady aplikácie bežia — otvor ich vedľa tohto rozhovoru a skúšaj podľa návodu.</span>
        <span className="ml-auto flex flex-wrap gap-2">
          {frontends.map((frontend) =>
            frontend.url ? (
              <a key={frontend.directory} href={frontend.url} target="_blank" rel="noreferrer" className={LINK}>
                <ExternalLink className="h-3.5 w-3.5" />
                Otvoriť: {frontend.label}
              </a>
            ) : null,
          )}
        </span>
      </div>
    );
  }
  return (
    <div role="status" aria-label="Živý náhľad" className={STRIP}>
      {url ? (
        <>
          <span>Živý náhľad aplikácie beží — otvor ho vedľa tohto rozhovoru a skúšaj podľa návodu.</span>
          <a href={url} target="_blank" rel="noreferrer" className={BUTTON}>
            <ExternalLink className="h-3.5 w-3.5" />
            Otvoriť živý náhľad
          </a>
        </>
      ) : (
        <span className="inline-flex items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Živý náhľad aplikácie sa spúšťa…
        </span>
      )}
    </div>
  );
}
