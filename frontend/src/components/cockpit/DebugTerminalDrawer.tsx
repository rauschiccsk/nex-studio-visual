// Debug escape hatch (F-007 §10): attach an interactive terminal to an
// agent's headless orchestrator session via claude --resume.

import { useState } from "react";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";

import { AgentTerminal } from "../AgentTerminal";
import { useAuthStore } from "../../store/authStore";
import { openDebugTerminalApi } from "../../services/api/pipeline";
import type { DebugAttachRole, PipelineActor } from "../../services/api/pipeline";
import { humanizeApiError, type HumanError } from "../../services/apiError";
import ErrorNote from "../common/ErrorNote";

// Slovak button labels per debug-attach role — never the raw charter slug ("ai-agent"/"auditor").
const TERMINAL_ROLE_LABELS: Record<DebugAttachRole, string> = {
  "ai-agent": "AI Agent",
  auditor: "Audítor",
};

// Raw-terminal peek (CR-V2-021, design §4.4.2): the break-glass attach to a v2 orchestrator session — the
// two agents only, as CHARTER-PATH SLUGS (hyphen). The board's current actor (a DB value, underscore) maps
// to its slug; default to the AI Agent (the warm doer session the Manažér most often peeks at).
const TERMINAL_ROLES: DebugAttachRole[] = ["ai-agent", "auditor"];

function asTerminalRole(actor: PipelineActor): DebugAttachRole {
  return actor === "auditor" ? "auditor" : "ai-agent";
}

interface Props {
  versionId: string;
  currentActor: PipelineActor;
}

export function DebugTerminalDrawer({ versionId, currentActor }: Props) {
  const token = useAuthStore((s) => s.token);
  const [expanded, setExpanded] = useState(false);
  const [role, setRole] = useState<DebugAttachRole>(asTerminalRole(currentActor));
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  const attach = async (r: DebugAttachRole) => {
    setLoading(true);
    setError(null);
    setSessionId(null);
    try {
      const session = await openDebugTerminalApi(versionId, r);
      setSessionId(session.id);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Pripojenie terminálu zlyhalo"));
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !sessionId && !loading) void attach(role);
  };

  const changeRole = (r: DebugAttachRole) => {
    setRole(r);
    void attach(r);
  };

  return (
    <div className="flex flex-col border-t border-[var(--color-border-default)] bg-[var(--color-canvas)]">
      <button
        onClick={toggle}
        className="flex items-center justify-between px-4 py-2 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
      >
        <span className="flex items-center gap-2">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
          Technický terminál (pre servis)
        </span>
        <span className="text-[10px] text-[var(--color-text-muted)]">{expanded ? "zbaliť" : "rozbaliť"}</span>
      </button>

      {expanded && (
        <div className="flex h-64 flex-col">
          <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-border-default)] px-4 py-1.5">
            <span className="text-[10px] text-[var(--color-text-muted)]">Pripojiť na:</span>
            {TERMINAL_ROLES.map((r) => (
              <button
                key={r}
                onClick={() => changeRole(r)}
                disabled={loading}
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  r === role
                    ? "bg-[var(--color-accent-primary)] text-white"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
                }`}
              >
                {TERMINAL_ROLE_LABELS[r]}
              </button>
            ))}
            {loading && <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />}
          </div>

          <ErrorNote
            error={error}
            className="border-b border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-4 py-1.5"
          />

          <div className="flex-1 overflow-hidden">
            {sessionId && token ? (
              <AgentTerminal sessionId={sessionId} token={token} />
            ) : (
              !error && (
                <div className="flex h-full items-center justify-center text-[11px] text-[var(--color-text-muted)]">
                  {loading ? "Pripájam terminál…" : "Vyber rolu pre pripojenie."}
                </div>
              )
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default DebugTerminalDrawer;
