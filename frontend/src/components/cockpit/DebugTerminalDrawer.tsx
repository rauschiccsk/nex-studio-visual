// Debug escape hatch (F-007 §10): attach an interactive terminal to an
// agent's headless orchestrator session via claude --resume.

import { useState } from "react";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";

import { AgentTerminal } from "../AgentTerminal";
import { useAuthStore } from "../../store/authStore";
import { openDebugTerminalApi } from "../../services/api/pipeline";
import type { AgentRole } from "../../services/api/agentTerminal";
import type { PipelineActor } from "../../services/api/pipeline";

const TERMINAL_ROLES: AgentRole[] = ["coordinator", "designer", "implementer", "auditor"];

function asTerminalRole(actor: PipelineActor): AgentRole {
  return (TERMINAL_ROLES as string[]).includes(actor) ? (actor as AgentRole) : "coordinator";
}

interface Props {
  versionId: string;
  currentActor: PipelineActor;
}

export function DebugTerminalDrawer({ versionId, currentActor }: Props) {
  const token = useAuthStore((s) => s.token);
  const [expanded, setExpanded] = useState(false);
  const [role, setRole] = useState<AgentRole>(asTerminalRole(currentActor));
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const attach = async (r: AgentRole) => {
    setLoading(true);
    setError(null);
    setSessionId(null);
    try {
      const session = await openDebugTerminalApi(versionId, r);
      setSessionId(session.id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Pripojenie terminálu zlyhalo");
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !sessionId && !loading) void attach(role);
  };

  const changeRole = (r: AgentRole) => {
    setRole(r);
    void attach(r);
  };

  return (
    <div className="flex flex-col border-t border-slate-800 bg-slate-950">
      <button
        onClick={toggle}
        className="flex items-center justify-between px-4 py-2 text-xs text-slate-400 hover:text-slate-200"
      >
        <span className="flex items-center gap-2">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
          Terminál (debug)
        </span>
        <span className="text-[10px] text-slate-600">{expanded ? "zbaliť" : "rozbaliť"}</span>
      </button>

      {expanded && (
        <div className="flex h-64 flex-col">
          <div className="flex flex-shrink-0 items-center gap-2 border-b border-slate-800 px-4 py-1.5">
            <span className="text-[10px] text-slate-500">Pripojiť na:</span>
            {TERMINAL_ROLES.map((r) => (
              <button
                key={r}
                onClick={() => changeRole(r)}
                disabled={loading}
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  r === role
                    ? "bg-primary-500/20 text-primary-300"
                    : "text-slate-500 hover:text-slate-300"
                }`}
              >
                {r}
              </button>
            ))}
            {loading && <Loader2 className="h-3 w-3 animate-spin text-slate-500" />}
          </div>

          {error && (
            <div className="border-b border-red-500/30 bg-red-500/10 px-4 py-1.5 text-[11px] text-red-400">
              {error}
            </div>
          )}

          <div className="flex-1 overflow-hidden">
            {sessionId && token ? (
              <AgentTerminal sessionId={sessionId} token={token} />
            ) : (
              !error && (
                <div className="flex h-full items-center justify-center text-[11px] text-slate-600">
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
