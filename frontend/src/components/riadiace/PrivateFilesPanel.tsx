// PrivateFilesPanel — DEV-44: what lies in the project's `private/` for the AI Agent, and a way to delete it.
//
// The files there are often personal (a real e-mail carries codes that cancel a job agent without a login), so
// the Manažér must SEE what is lying on the server and remove it when the agent is done — without a terminal.
// The list is the folder as it is (also what the agent put there itself); who attached a file comes from the
// build history. It reloads whenever the board gains an attach/delete record, so another tab's upload shows too.
// Shown only while the folder holds something — the attach button lives in the boxes the Manažér writes in.

import { useEffect, useState } from "react";
import { FolderLock, Trash2 } from "lucide-react";

import type { PipelineBoard } from "@/services/api/pipeline";
import { deletePrivateFileApi, listPrivateFilesApi, type PrivateFile } from "@/services/api/projectFiles";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import {
  CONFIRM_DELETE_LABEL,
  DELETE_LABEL,
  FILES_HINT,
  FILES_TITLE,
  privateFileEvents,
  uploadedLabel,
} from "./privateFiles";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
}

export default function PrivateFilesPanel({ board, versionId }: Props) {
  const [files, setFiles] = useState<PrivateFile[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const events = privateFileEvents(board);

  useEffect(() => {
    let cancelled = false;
    listPrivateFilesApi(versionId)
      .then((listing) => {
        if (!cancelled) setFiles(listing.files);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(humanizeApiError(err, "Zoznam súborov pre agenta sa nepodarilo načítať"));
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, events]);

  async function remove(path: string) {
    setError(null);
    setBusy(true);
    try {
      setFiles((await deletePrivateFileApi(versionId, path)).files);
      setConfirming(null);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Súbor sa nepodarilo zmazať"));
    } finally {
      setBusy(false);
    }
  }

  if (files.length === 0 && !error) return null;

  return (
    <div className="flex flex-col gap-1.5 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-2">
      <div className="flex items-center gap-2 text-xs font-semibold text-[var(--color-text-secondary)]">
        <FolderLock className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
        <span>
          {FILES_TITLE} ({files.length})
        </span>
      </div>
      <p className="text-[11px] text-[var(--color-text-muted)]">{FILES_HINT}</p>
      <ul className="flex flex-col gap-1">
        {files.map((f) => (
          <li key={f.path} className="flex items-center gap-2 text-xs">
            <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">{f.path}</span>
            <span className="shrink-0 text-[var(--color-text-muted)]">
              {f.size_label} · {uploadedLabel(f.uploaded_by, f.uploaded_at)}
            </span>
            <button
              type="button"
              onClick={() => (confirming === f.path ? remove(f.path) : setConfirming(f.path))}
              disabled={busy}
              className="flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
            >
              <Trash2 className="h-3 w-3" aria-hidden="true" />
              {confirming === f.path ? CONFIRM_DELETE_LABEL : DELETE_LABEL}
            </button>
          </li>
        ))}
      </ul>
      <ErrorNote error={error} />
    </div>
  );
}
