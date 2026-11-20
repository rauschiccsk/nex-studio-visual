// PrivateFilesPanel — DEV-44: what lies in the project's `private/` for the AI Agent, and a way to delete it.
//
// The files there are often personal (a real e-mail carries codes that cancel a job agent without a login), so
// the Manažér must SEE what is lying on the server and remove it when the agent is done — without a terminal.
// The list is the folder as it is (also what the agent put there itself); who attached a file comes from the
// build history. It reloads whenever the board gains an attach/delete record, so another tab's upload shows too.
// Shown only while the folder holds something — the attach button lives in the boxes the Manažér writes in.
//
// DEV-441 — the rows sit as close as the text allows and the heading folds the list away; it takes room from the
// conversation („aby bolo možné aj schovať tento zoznam“). The choice is kept in the browser.
// DEV-443 — what the agent put into a folder is one line per folder, opened on request and deleted at once
// (20.11.2026 its unpacked copy of nex-app-kit made 190 of 196 lines).
// DEV-446 — the heading is „Prílohy“ and counts what a person attached; the Staviteľ's own files stand under them,
// folded (Director 20.11.2026: „tam majú byť prílohy, ktoré som zadal ja, po druhé počet 287“).

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FolderLock, Trash2 } from "lucide-react";

import type { PipelineBoard } from "@/services/api/pipeline";
import {
  deletePrivateFileApi,
  deletePrivateFolderApi,
  listPrivateFilesApi,
  type PrivateFile,
  type PrivateFiles,
} from "@/services/api/projectFiles";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import {
  CONFIRM_DELETE_FOLDER_LABEL,
  CONFIRM_DELETE_LABEL,
  DELETE_LABEL,
  FILES_FOLDED_KEY,
  AGENT_FILES_TITLE,
  ATTACHMENTS_TITLE,
  FILES_HINT,
  HIDE_FILES_LABEL,
  SHOW_FILES_LABEL,
  agentFileCount,
  folderLabel,
  groupPrivateFiles,
  splitAttachments,
  privateFileEvents,
  uploadedLabel,
  type PrivateFilesRow,
} from "./privateFiles";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
}

const DELETE_BUTTON =
  "flex shrink-0 items-center gap-1 rounded px-1.5 text-[11px] leading-4 font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50";

function readFolded(): boolean {
  try {
    return window.localStorage.getItem(FILES_FOLDED_KEY) === "1";
  } catch {
    return false;
  }
}

function keepFolded(folded: boolean): void {
  try {
    if (folded) window.localStorage.setItem(FILES_FOLDED_KEY, "1");
    else window.localStorage.removeItem(FILES_FOLDED_KEY);
  } catch {
    /* a browser without storage folds for this screen only */
  }
}

export default function PrivateFilesPanel({ board, versionId }: Props) {
  const [files, setFiles] = useState<PrivateFile[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [folded, setFolded] = useState(readFolded);
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  // DEV-446: the Staviteľ's own files wait folded under the attachments.
  const [agentOpen, setAgentOpen] = useState(false);
  const events = privateFileEvents(board);

  useEffect(() => {
    let cancelled = false;
    listPrivateFilesApi(versionId)
      .then((listing) => {
        if (!cancelled) setFiles(listing.files);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(humanizeApiError(err, "Zoznam súborov pre Staviteľa sa nepodarilo načítať"));
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, events]);

  async function remove(call: () => Promise<PrivateFiles>, failed: string) {
    setError(null);
    setBusy(true);
    try {
      setFiles((await call()).files);
      setConfirming(null);
    } catch (err: unknown) {
      setError(humanizeApiError(err, failed));
    } finally {
      setBusy(false);
    }
  }

  function toggleFolded() {
    setFolded((was) => {
      keepFolded(!was);
      return !was;
    });
  }

  function toggleOpened(path: string) {
    setOpened((was) => {
      const next = new Set(was);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  if (files.length === 0 && !error) return null;

  const fileLine = (f: PrivateFile, indent = false) => (
    <li key={f.path} className={`flex items-center gap-2 text-xs leading-4 ${indent ? "pl-5" : ""}`}>
      <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">{f.path}</span>
      <span className="shrink-0 text-[var(--color-text-muted)]">
        {f.size_label} · {uploadedLabel(f.uploaded_by, f.uploaded_at)}
      </span>
      {!indent && (
        <button
          type="button"
          onClick={() =>
            confirming === f.path
              ? remove(() => deletePrivateFileApi(versionId, f.path), "Súbor sa nepodarilo zmazať")
              : setConfirming(f.path)
          }
          disabled={busy}
          className={DELETE_BUTTON}
        >
          <Trash2 className="h-3 w-3" aria-hidden="true" />
          {confirming === f.path ? CONFIRM_DELETE_LABEL : DELETE_LABEL}
        </button>
      )}
    </li>
  );

  const folderLine = (row: Extract<PrivateFilesRow, { kind: "folder" }>) => (
    <li key={row.path} className="flex flex-col">
      <div className="flex items-center gap-2 text-xs leading-4">
        <button
          type="button"
          onClick={() => toggleOpened(row.path)}
          aria-expanded={opened.has(row.path)}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
        >
          {opened.has(row.path) ? (
            <ChevronDown className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
          )}
          <span className="truncate font-mono text-[var(--color-text-primary)]">{row.path}</span>
        </button>
        <span className="shrink-0 text-[var(--color-text-muted)]">{folderLabel(row)}</span>
        <button
          type="button"
          onClick={() =>
            confirming === row.path
              ? remove(() => deletePrivateFolderApi(versionId, row.path), "Priečinok sa nepodarilo zmazať")
              : setConfirming(row.path)
          }
          disabled={busy}
          className={DELETE_BUTTON}
        >
          <Trash2 className="h-3 w-3" aria-hidden="true" />
          {confirming === row.path ? CONFIRM_DELETE_FOLDER_LABEL : DELETE_LABEL}
        </button>
      </div>
      {opened.has(row.path) && <ul className="flex flex-col">{row.files.map((f) => fileLine(f, true))}</ul>}
    </li>
  );

  // DEV-446: the heading counts the attachments only; the Staviteľ's files stand apart, folded.
  const { attached, agent } = splitAttachments(groupPrivateFiles(files));

  return (
    <div className="flex flex-col gap-1 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-1.5">
      <button
        type="button"
        onClick={toggleFolded}
        aria-expanded={!folded}
        title={folded ? SHOW_FILES_LABEL : HIDE_FILES_LABEL}
        className="flex items-center gap-2 self-start text-xs font-semibold text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
      >
        {folded ? (
          <ChevronRight className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
        )}
        <FolderLock className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
        <span>
          {ATTACHMENTS_TITLE} ({attached.length})
        </span>
      </button>
      {!folded && (
        <>
          <p className="text-[11px] leading-4 text-[var(--color-text-muted)]">{FILES_HINT}</p>
          {attached.length > 0 && <ul className="flex flex-col">{attached.map((f) => fileLine(f))}</ul>}
          {agent.length > 0 && (
            <div className="flex flex-col">
              <button
                type="button"
                onClick={() => setAgentOpen((was) => !was)}
                aria-expanded={agentOpen}
                className="flex items-center gap-1 self-start text-xs leading-4 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              >
                {agentOpen ? (
                  <ChevronDown className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                ) : (
                  <ChevronRight className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                )}
                <span>
                  {AGENT_FILES_TITLE} · počet súborov: {agentFileCount(agent)}
                </span>
              </button>
              {agentOpen && (
                <ul className="flex flex-col pl-4">
                  {agent.map((row) => (row.kind === "file" ? fileLine(row.file) : folderLine(row)))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
