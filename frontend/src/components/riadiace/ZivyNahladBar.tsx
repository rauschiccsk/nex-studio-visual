// ZivyNahladBar — the live preview of the app during the Vizuál step, opened in its own window (DEV-120).
//
// Director 15.10.2026: „Radšej odstráňme záložku "Vizuál". Aj tak nemôžem používať, lebo ak otvorím tu vizuál potom
// nevidím návod čo treba otestovať. Preto vizuál ja púšťam v Chrome a tak viem s tým pracovať.“ The Vizuál page
// left the sidebar; the one thing only it had — the preview's address with „Otvoriť vo vlastnom okne“ — stands here,
// beside the conversation that says what to check. Shown only in the Vizuál step: the sandbox is torn down when
// the step advances, so an address left over from a past run never reads as live.

import { ExternalLink, Loader2 } from "lucide-react";

import type { PipelineBoard } from "@/services/api/pipeline";

interface Props {
  board: PipelineBoard | null;
}

export default function ZivyNahladBar({ board }: Props) {
  if (board?.state?.current_stage !== "vizual") return null;
  const url = board.vizual_url ?? null;
  return (
    <div
      role="status"
      aria-label="Živý náhľad"
      className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)]"
    >
      {url ? (
        <>
          <span>Živý náhľad aplikácie beží — otvor ho vedľa tohto rozhovoru a skúšaj podľa návodu.</span>
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1 font-medium transition-colors hover:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11"
          >
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
