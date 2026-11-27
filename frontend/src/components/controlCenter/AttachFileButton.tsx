// AttachFileButton — DEV-44: the Manažér hands the AI Agent a file (a sample e-mail, an invoice, an export).
//
// Until DEV-44 an agent that needed such a file asked for it "in private/ on ANDROS" and Poradca advised `scp`
// over Tailscale — a terminal step a Manažér cannot take (Career Asistent 0.1.0, 09.10.2026). Here it is one
// click: the backend stores the file in the project's `private/` (git never sees it) and returns the line that
// says where it lies; this button puts that line into the message being written, so the agent gets the path
// with the Manažér's own words. Used in the answer box and in the conversation — one wording, one behaviour.

import { useRef, useState } from "react";
import { Loader2, Paperclip } from "lucide-react";

import { uploadPrivateFileApi } from "@/services/api/projectFiles";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import { ATTACH_HINT, ATTACH_LABEL, ATTACHING_LABEL } from "./privateFiles";

interface Props {
  versionId: string;
  /** Receives the line for the message — where the agent finds the file. */
  onAttached: (line: string) => void;
  disabled?: boolean;
}

export default function AttachFileButton({ versionId, onAttached, disabled }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  async function upload(file: File) {
    setError(null);
    setUploading(true);
    try {
      const stored = await uploadPrivateFileApi(versionId, file);
      onAttached(stored.answer_line);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Súbor sa nepodarilo priložiť"));
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="flex shrink-0 flex-col items-end gap-1">
      <input
        ref={input}
        type="file"
        className="hidden"
        data-testid="attach-file-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
        }}
      />
      <button
        type="button"
        title={ATTACH_HINT}
        onClick={() => input.current?.click()}
        disabled={disabled || uploading}
        className="flex h-9 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {uploading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {uploading ? ATTACHING_LABEL : ATTACH_LABEL}
      </button>
      <ErrorNote error={error} />
    </div>
  );
}
