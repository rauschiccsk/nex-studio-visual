// One Poradca answer (ICCINT-167): progress, text, duration and cost, and two buttons when a change is needed.
//
// The buttons appear only when the answer carries the relevant block (Poradca charter, part 4) — and „Vložiť do
// Riadiaceho centra" is grey with a reason when the build field rejects an instruction. Nothing is sent by itself:
// pokyn odošle človek v Riadiacom centre; požiadavka ide len do Zásobníka — verzia z nej nevzniká, o verziách
// rozhoduje Director (DEV-29). DEV-119: a proposed ticket opens a preview; the person founds it in Plane.
// DEV-218: the tickets the agent asked for — the cockpit reads them from Plane and attaches them as files; the lines
// that say where go into the answer for the agent, which the Manažér sends himself.

import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Archive, ChevronDown, ChevronRight, Loader2, Paperclip, Send, Square, Ticket } from "lucide-react";

import { SpecMarkdown } from "@/components/markdown/SpecMarkdown";
import ErrorNote from "@/components/common/ErrorNote";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useCodePaths } from "@/hooks/useCodePaths";
import { attachAgentTicketsApi, saveRequestToBacklogApi, stopPoradcaApi } from "@/services/api/poradca";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { handOffInstruction } from "@/lib/poradcaHandoff";
import { answerForDisplay, CHOICE_HREF, formatCost, formatDuration, stepLabel } from "@/lib/poradcaAnswer";
import { InlineChoice } from "@/components/poradca/ChoiceBadge";
import PoradcaTicketPanel from "@/components/poradca/PoradcaTicketPanel";
import { modelDisplayName } from "@/utils/modelLabel";
import type { PoradcaMessage, PoradcaVersionInfo } from "@/types/poradca";

interface Props {
  message: PoradcaMessage;
  /** The version the conversation is currently about — `null` = the whole project. */
  scopeVersion: PoradcaVersionInfo | null;
}

export default function PoradcaAnswer({ message, scopeVersion }: Props) {
  const navigate = useNavigate();
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);
  const projectSlug = useActiveContextStore((s) => s.selectedProject?.slug ?? null);
  // DEV-88 — a file of the project the answer names is a link into NS Code, at the line it names.
  const codePaths = useCodePaths(projectSlug);
  // DEV-29: the REQ-N the request was saved as — from the answer itself, or from the click just now.
  const [savedNumber, setSavedNumber] = useState<number | null>(message.captured_backlog_number ?? null);
  const [showSteps, setShowSteps] = useState(false);
  // DEV-119: the ticket founded from this answer — from the answer itself, or from the click just now.
  const [ticket, setTicket] = useState<{ identifier: string; url: string } | null>(
    message.plane_identifier ? { identifier: message.plane_identifier, url: message.plane_url ?? "" } : null,
  );
  const [ticketOpen, setTicketOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const inFlight = useRef(false);
  const agentTickets = message.agent_tickets ?? [];
  const steps = message.steps ?? [];
  // DEV-83: the choices the answer marks, checked by the server against the card they name.
  const choices = message.choices ?? [];
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
    if (message.instruction) handOff(message.instruction);
  }

  // DEV-135: the instruction and the recommended answer to the agent's question go the same way — into the box of
  // Riadiace centrum that takes the Manažér's text now; nothing is sent.
  function handOff(text: string) {
    if (!scopeVersion) return;
    try {
      handOffInstruction(scopeVersion.id, text);
    } catch (e: unknown) {
      setError({ message: e instanceof Error ? e.message : "Vloženie zlyhalo" });
      return;
    }
    setSelectedVersion({ versionId: scopeVersion.id, versionNumber: scopeVersion.version_number });
    navigate("/riadiace-centrum");
  }

  async function saveToBacklog() {
    if (inFlight.current) return; // a second click before the re-render must not save it twice
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const saved = await saveRequestToBacklogApi(message.id);
      setSavedNumber(saved.number);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Uloženie do Zásobníka zlyhalo"));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  // DEV-218 — the cockpit reads the tickets from Plane now and stores them for the agent; the lines go to the answer.
  // The button is grey while it runs, so a second click attaches nothing twice.
  async function attachTickets() {
    setAttaching(true);
    setError(null);
    try {
      const attached = await attachAgentTicketsApi(message.id);
      handOff(attached.answer_text);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Tiket sa nepodarilo priložiť"));
    } finally {
      setAttaching(false);
    }
  }

  const instructionBlocked = !scopeVersion
    ? "Rozhovor je o celom projekte — hore vyber verziu, do ktorej stavby pokyn patrí."
    : scopeVersion.instruction_open
      ? null
      : (scopeVersion.instruction_closed_reason ?? "Pole Riadiaceho centra teraz pokyn neprijme.");

  // DEV-135 / DEV-205 — „Vložiť do odpovede“ where the answer names the answer for the agent (DEV-208: only there).
  const answerInsert = { insert: handOff, blocked: instructionBlocked };
  const hasActions = !!(message.instruction || message.backlog_request || message.ticket_proposal || agentTickets.length);

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
        <SpecMarkdown
          codePaths={codePaths}
          projectSlug={projectSlug ?? undefined}
          body={answerForDisplay(message.content)}
          className="prose-sm text-sm text-[var(--color-text-primary)]"
          components={{
            a({ href, children, ...props }) {
              if (href?.startsWith(CHOICE_HREF)) {
                return (
                  <InlineChoice href={href} choices={choices} answer={answerInsert}>
                    {children}
                  </InlineChoice>
                );
              }
              return (
                <a href={href} {...props}>
                  {children}
                </a>
              );
            },
          }}
        />
      )}
      {message.status === "failed" && (
        <p className="text-xs text-[var(--color-state-error-fg)]">{message.error ?? "Odpoveď zlyhala."}</p>
      )}
      {message.status === "stopped" && <p className="text-xs text-[var(--color-text-muted)]">Zastavené.</p>}

      {!running && hasActions && (
        <div className="mt-2 flex flex-wrap gap-2">
          {agentTickets.length > 0 && (
            <button
              type="button"
              onClick={() => void attachTickets()}
              disabled={!!instructionBlocked || attaching}
              title={
                instructionBlocked ??
                "Kokpit vezme tiket priamo z Plane, priloží ho agentovi ako súbor a do odpovede doplní, kde leží — " +
                  "odpoveď odošleš sám."
              }
              className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-primary-600"
            >
              {attaching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Paperclip className="h-3.5 w-3.5" />}
              {`Priložiť k odpovedi agentovi: ${agentTickets.join(", ")}`}
            </button>
          )}
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
          {message.backlog_request && savedNumber == null && (
            <button
              type="button"
              onClick={() => void saveToBacklog()}
              disabled={busy}
              title="Požiadavka sa zapíše do Zásobníka projektu. Do ktorej verzie pôjde, rozhodneš sám."
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Archive className="h-3.5 w-3.5" />} Uložiť do
              Zásobníka
            </button>
          )}
          {message.backlog_request && savedNumber != null && (
            <span className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
              {`Uložené v Zásobníku ako REQ-${savedNumber}.`}
              {projectSlug && (
                <button
                  type="button"
                  onClick={() => navigate(`/projects/${projectSlug}/backlog`)}
                  className="rounded-lg border border-[var(--color-border-default)] px-2 py-1 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
                >
                  Otvoriť Zásobník
                </button>
              )}
            </span>
          )}
          {message.ticket_proposal && !ticket && (
            <button
              type="button"
              aria-expanded={ticketOpen}
              onClick={() => setTicketOpen((v) => !v)}
              title={
                message.ticket_proposal.kind === "nsv"
                  ? "Tiket pre problém kokpitu — rieši ho Andros Dedo. Najprv uvidíš náhľad."
                  : "Tiket projektu v Plane — návrh, verziu nezakladá. Najprv uvidíš náhľad."
              }
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
            >
              <Ticket className="h-3.5 w-3.5" /> Založiť tiket
            </button>
          )}
          {ticket && (
            <span className="flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
              Tiket{" "}
              <a href={ticket.url} target="_blank" rel="noreferrer" className="font-mono underline">
                {ticket.identifier}
              </a>{" "}
              je založený.
            </span>
          )}
        </div>
      )}
      {ticketOpen && !ticket && (
        <PoradcaTicketPanel
          messageId={message.id}
          onFounded={(t) => {
            setTicket(t);
            setTicketOpen(false);
          }}
          onClose={() => setTicketOpen(false)}
        />
      )}
      {instructionBlocked && (message.instruction || agentTickets.length > 0) && !running && (
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
