// Jedna odpoveď Poradcu (ICCINT-167): priebeh, text, trvanie a cena, a dve tlačidlá, keď treba zmenu.
//
// Tlačidlá sa ukážu len vtedy, keď odpoveď nesie príslušný blok (charta Poradcu, časť 4) — a „Vložiť do
// Riadiaceho centra" je zašednuté s dôvodom, keď pole stavby pokyn neprijme. Nič sa neodošle samo:
// pokyn odošle človek v Riadiacom centre, verzia vznikne ako koncept bez spustenej stavby.

import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, GitBranchPlus, Loader2, Send, Square } from "lucide-react";

import { SpecMarkdown } from "@/components/markdown/SpecMarkdown";
import ErrorNote from "@/components/common/ErrorNote";
import { useActiveContextStore } from "@/store/activeContextStore";
import { newVersionFromPoradcaApi, stopPoradcaApi } from "@/services/api/poradca";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { insertInstructionDraft } from "@/lib/poradcaHandoff";
import { answerForDisplay, formatCost, formatDuration, stepLabel } from "@/lib/poradcaAnswer";
import { modelDisplayName } from "@/utils/modelLabel";
import type { PoradcaMessage, PoradcaVersionInfo } from "@/types/poradca";

interface Props {
  message: PoradcaMessage;
  /** Verzia, o ktorej sa rozhovor práve rozpráva — `null` = celý projekt. */
  scopeVersion: PoradcaVersionInfo | null;
}

export default function PoradcaAnswer({ message, scopeVersion }: Props) {
  const navigate = useNavigate();
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);
  const [showSteps, setShowSteps] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const inFlight = useRef(false);
  const steps = message.steps ?? [];
  const running = message.status === "running";

  async function stop() {
    setError(null);
    try {
      await stopPoradcaApi(message.id);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Zastavenie zlyhalo"));
    }
  }

  function insert() {
    if (!scopeVersion || !message.instruction) return;
    try {
      insertInstructionDraft(scopeVersion.id, message.instruction);
    } catch (e: unknown) {
      setError({ message: e instanceof Error ? e.message : "Vloženie zlyhalo" });
      return;
    }
    setSelectedVersion({ versionId: scopeVersion.id, versionNumber: scopeVersion.version_number });
    navigate("/riadiace-centrum");
  }

  async function newVersion() {
    if (inFlight.current) return; // druhé kliknutie pred prekreslením nesmie založiť druhú verziu
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const v = await newVersionFromPoradcaApi(message.id);
      setSelectedVersion({ versionId: v.version_id, versionNumber: v.version_number });
      navigate(`/projects/${v.project_slug}/versions/${v.version_id}`);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Založenie verzie zlyhalo"));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const instructionBlocked = !scopeVersion
    ? "Rozhovor je o celom projekte — hore vyber verziu, do ktorej stavby pokyn patrí."
    : scopeVersion.instruction_open
      ? null
      : (scopeVersion.instruction_closed_reason ?? "Pole Riadiaceho centra teraz pokyn neprijme.");

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2">
      <div className="mb-1 text-[11px] font-semibold text-[var(--color-text-secondary)]">Poradca</div>

      {running && (
        <div className="space-y-1" aria-live="polite">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Poradca pracuje…
            </span>
            <button
              type="button"
              onClick={() => void stop()}
              className="flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
            >
              <Square className="h-3 w-3" /> Zastaviť
            </button>
          </div>
          <ul className="space-y-0.5">
            {steps.map((s, i) => (
              <li key={i} className="truncate text-[11px] text-[var(--color-text-muted)]">
                {stepLabel(s.tool, s.target ?? "")}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!running && message.content && (
        <SpecMarkdown body={answerForDisplay(message.content)} className="prose-sm text-sm text-[var(--color-text-primary)]" />
      )}
      {message.status === "failed" && (
        <p className="text-xs text-[var(--color-state-error-fg)]">{message.error ?? "Odpoveď zlyhala."}</p>
      )}
      {message.status === "stopped" && <p className="text-xs text-[var(--color-text-muted)]">Zastavené.</p>}

      {!running && (message.instruction || message.new_version_request) && (
        <div className="mt-2 flex flex-wrap gap-2">
          {message.instruction && (
            <button
              type="button"
              onClick={insert}
              disabled={!!instructionBlocked}
              title={instructionBlocked ?? "Vloží pokyn do poľa Riadiaceho centra — odošleš ho sám."}
              className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-primary-600"
            >
              <Send className="h-3.5 w-3.5" /> Vložiť do Riadiaceho centra
            </button>
          )}
          {message.new_version_request && (
            <button
              type="button"
              onClick={() => void newVersion()}
              disabled={busy}
              title="Požiadavka pôjde do zásobníka a vznikne koncept verzie — stavbu spustíš sám."
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
            >
              <GitBranchPlus className="h-3.5 w-3.5" />
              {message.captured_version_id ? "Otvoriť založenú verziu" : "Založiť novú verziu z tejto požiadavky"}
            </button>
          )}
        </div>
      )}
      {instructionBlocked && message.instruction && !running && (
        <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">{instructionBlocked}</p>
      )}
      {error && <ErrorNote error={error} />}

      {!running && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-muted)]">
          {message.duration_seconds != null && <span>trvalo {formatDuration(message.duration_seconds)}</span>}
          {message.model && (
            <span>
              {formatCost(message.cost)} · {modelDisplayName(message.model)}
            </span>
          )}
          {steps.length > 0 && (
            <button
              type="button"
              onClick={() => setShowSteps((v) => !v)}
              className="flex items-center gap-0.5 hover:text-[var(--color-text-secondary)]"
            >
              {showSteps ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Čo Poradca robil ({steps.length})
            </button>
          )}
        </div>
      )}
      {!running && showSteps && (
        <ul className="mt-1 space-y-0.5">
          {steps.map((s, i) => (
            <li key={i} className="truncate text-[11px] text-[var(--color-text-muted)]">
              {stepLabel(s.tool, s.target ?? "")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
