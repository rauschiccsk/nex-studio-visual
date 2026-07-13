/**
 * VizualPage — the cockpit "Vizuál" surface (CR-1, nex-studio-visual; spec §3.D).
 *
 * The live app preview the Manažér opens on the SECOND monitor: the project's running dev-server embedded in a
 * full-height iframe while the AI edits the frontend and Vite HMR reflects each change in <1 s. The change
 * requests themselves are typed in the Riadiace centrum (monitor 1) — this surface is the WALK: it only SHOWS
 * the live app, it does not chat.
 *
 * A thin, HONEST FE over the pipeline board (the SAME `usePipelineWs` hook the Riadiace centrum uses): the
 * board carries `vizual_url` (the preview URL, recorded on entry into the `vizual` stage) + the current stage.
 * No new WS client, no new mutating call.
 *
 * Honest-by-construction states:
 *   1. no project pinned / no version pinned → a guard prompt (mirrors SpecifikaciaPage's "pin a project").
 *   2. IN the `vizual` stage + `vizual_url` set → the full-height live iframe + a header (app name + "Otvoriť
 *      vo vlastnom okne" + "Obnoviť"). The iframe is gated on being in the vizual stage on purpose: the sandbox
 *      is torn down when the phase advances, so a URL left over from a past run must never render as "live".
 *   3. IN the `vizual` stage but `vizual_url` not yet recorded → a "Živý náhľad sa spúšťa…" loading state.
 *   4. NOT in the `vizual` stage → a plain note that the live preview appears during the Vizuál step, with a
 *      link back to the Riadiace centrum.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Monitor, ExternalLink, RefreshCw, FolderOpen } from "lucide-react";

import { useActiveContextStore } from "@/store/activeContextStore";
import { usePipelineWs } from "@/hooks/usePipelineWs";

export default function VizualPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const versionId = selectedVersion?.versionId ?? null;

  // Live board over the EXISTING pipeline WS — same hook + usage as RiadiaceCentrumPage (INVARIANT: no new WS
  // client). Carries `vizual_url` (the preview URL) + `state.current_stage`.
  const { board } = usePipelineWs(versionId);

  // Force-remount key for the "Obnoviť" reload. The iframe is cross-origin (the `vizual-<slug>` route), so
  // `contentWindow.location.reload()` would throw a SecurityError — remounting the iframe reloads it cleanly.
  const [reloadKey, setReloadKey] = useState(0);

  // ── Guard: no project pinned (mirrors SpecifikaciaPage) ──────────────────────
  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-[var(--color-canvas)] p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Živý vizuál beží nad konkrétnym projektom. Otvor <span className="font-mono">Projekty</span> a pripni
          projekt (a verziu).
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          → Otvor Projekty
        </button>
      </div>
    );
  }

  // ── Guard: project pinned but no version sub-selection (the preview is version-scoped) ───
  if (!versionId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Vyber verziu projektu <span className="font-medium">{selectedProject.name}</span> (pin v Projektoch) —
          živý vizuál je viazaný na konkrétnu verziu.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          → Otvor Projekty
        </button>
      </div>
    );
  }

  const stage = board?.state?.current_stage;
  const inVizual = stage === "vizual";
  const vizualUrl = board?.vizual_url ?? null;

  // ── State: live preview available — the running dev-server in a full-height iframe ───────
  if (inVizual && vizualUrl) {
    return (
      <div className="flex h-full flex-col bg-[var(--color-canvas)]">
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-2.5">
          <Monitor className="h-4 w-4 text-[var(--color-text-muted)]" />
          <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">Vizuál</h1>
          <span className="text-[var(--color-text-muted)]">·</span>
          <span className="truncate text-xs text-[var(--color-text-secondary)]">
            {selectedProject.name}
            {selectedVersion && (
              <span className="text-[var(--color-text-muted)]"> · {selectedVersion.versionNumber}</span>
            )}
          </span>
          <div className="ml-auto flex flex-shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => setReloadKey((k) => k + 1)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Obnoviť
            </button>
            <a
              href={vizualUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Otvoriť vo vlastnom okne
            </a>
          </div>
        </div>
        <iframe
          key={reloadKey}
          title="Živý vizuál"
          src={vizualUrl}
          sandbox="allow-scripts allow-same-origin allow-forms"
          className="w-full flex-1 border-0 bg-white"
        />
      </div>
    );
  }

  // ── State: in the Vizuál phase, sandbox still spinning up (no URL recorded yet) ──────────
  if (inVizual) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <Monitor className="h-10 w-10 animate-pulse text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Živý náhľad sa spúšťa…</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Pripravujem živý vizuál projektu <span className="font-medium">{selectedProject.name}</span>. Chvíľu to
          potrvá — hneď ako bude pripravený, zobrazí sa tu.
        </p>
      </div>
    );
  }

  // ── State: not in the Vizuál phase — the live preview only exists during the Vizuál step ─
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 bg-[var(--color-canvas)] p-6 text-center">
      <Monitor className="h-10 w-10 text-[var(--color-text-muted)]" />
      <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Živý vizuál tu zatiaľ nie je</h2>
      <p className="max-w-md text-xs text-[var(--color-text-muted)]">
        Živý náhľad projektu sa zobrazí počas kroku <span className="font-medium">Vizuál</span> — vtedy AI ukáže
        bežiacu aplikáciu a ty ju priamo prejdeš a pýtaš si zmeny. Zmeny píšeš v Riadiacom centre.
      </p>
      <button
        onClick={() => navigate("/riadiace-centrum")}
        className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
      >
        → Otvor Riadiace centrum
      </button>
    </div>
  );
}
