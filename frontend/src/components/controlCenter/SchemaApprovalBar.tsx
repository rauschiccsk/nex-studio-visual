// SchemaApprovalBar — DEV-7: the AI Agent needs a database change in Programovanie, and only Ri may approve it.
//
// icc/SCHEMA_GOVERNANCE.md gives the approval of a database change to Ri alone, and makes the approved schema in
// the Knowledge Base the single source of truth. Until DEV-7 the agent asked in free text, the Director answered
// in free text and Dedo copied the schema into the Knowledge Base from a terminal (dedo-home, 02.10.2026). Here
// the approval is one click — and the click is what writes the Knowledge Base (the cockpit does it, server-side).
//
// Honest-by-construction: renders only while the backend offers `schvalit_schemu`. The button is greyed out, with
// the reason, for anyone whose role may not approve; the answer box below (BlockRecoveryBar) stays open to say
// „no, do it differently".

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Database, FileText } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { useAuthStore } from "@/store/authStore";
import ErrorNote from "@/components/common/ErrorNote";
import { schemaChangeSentence, schemaDocLink, SCHEMA_APPROVER_ONLY } from "./databaseSchema";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  onBoard: (board: PipelineBoard) => void;
}

export default function SchemaApprovalBar({ board, versionId, onBoard }: Props) {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.user?.role);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  if (!board?.available_actions?.includes("schvalit_schemu")) return null;

  const schema = board.database_schema ?? null;
  const mayApprove = !!role && role === (schema?.approver_role ?? "ri");

  async function approve() {
    setError(null);
    setSubmitting(true);
    try {
      onBoard(await postPipelineActionApi(versionId, { action: "schvalit_schemu" }));
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Schválenie štruktúry databázy zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-accent-primary)]">
        <Database className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>AI Agent potrebuje zmeniť štruktúru databázy</span>
      </div>
      {board.state?.next_action && (
        <p className="text-xs text-[var(--color-text-secondary)]">{board.state.next_action}</p>
      )}
      {schema && <p className="text-xs text-[var(--color-text-muted)]">{schemaChangeSentence(schema)}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {schema && (
          <button
            type="button"
            onClick={() => navigate(schemaDocLink(schema))}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <FileText className="h-3.5 w-3.5" aria-hidden="true" />
            Prezrieť štruktúru databázy
          </button>
        )}
        <button
          type="button"
          onClick={approve}
          disabled={submitting || !mayApprove}
          className="ml-auto shrink-0 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Schvaľujem…" : "Schváliť štruktúru databázy"}
        </button>
      </div>
      {!mayApprove && <p className="text-xs text-[var(--color-text-muted)]">{SCHEMA_APPROVER_ONLY}</p>}
      <ErrorNote error={error} />
    </div>
  );
}
