// The screenshots and documents waiting in a box to go with the message — a small preview each (a document by its
// name), removable. DEV-52 drew them in Poradca's box; DEV-534 the Staviteľ's box shows them the same way, so the three
// places a screenshot is pasted (Poradca, Nápady, the conversation with the Staviteľ) look alike.
import { FileText, X } from "lucide-react";

import type { PendingAttachment } from "@/lib/poradcaAttachments";

export default function PendingAttachmentChips({
  attachments,
  onRemove,
  disabled,
}: {
  attachments: PendingAttachment[];
  onRemove: (key: string) => void;
  disabled?: boolean;
}) {
  if (attachments.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-2" data-testid="pending-attachments">
      {attachments.map((a) => (
        <div key={a.key} className="relative overflow-hidden rounded border border-[var(--color-border-default)]">
          {a.url ? (
            <img src={a.url} alt={a.file.name} className="h-16 max-w-[8rem] object-cover" />
          ) : (
            <span className="flex h-16 max-w-[12rem] items-center gap-1.5 py-1 pl-2 pr-6 text-xs text-[var(--color-text-secondary)]">
              <FileText className="h-4 w-4 flex-shrink-0" aria-hidden />
              <span className="break-all">{a.file.name}</span>
            </span>
          )}
          <button
            type="button"
            aria-label={`Odobrať ${a.file.name}`}
            onClick={() => onRemove(a.key)}
            disabled={disabled}
            className="absolute right-0.5 top-0.5 rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ))}
    </div>
  );
}
