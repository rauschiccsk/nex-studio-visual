// A finished version in the Riadiace centrum (ICCINT-167): instead of the message field, an „Opýtaj sa Poradcu" button.
//
// The build agent does not work on a finished version — the message would stay unanswered (the backend rejects it).
// Questions about the project belong to Poradca, who runs alongside the build and only reads; a change from its answer
// is filed as a new version. The button opens Poradca with this version preselected.

import { useNavigate } from "react-router-dom";
import { MessageSquare } from "lucide-react";

interface Props {
  versionId: string;
}

export default function OpytajSaPoradcuBar({ versionId }: Props) {
  const navigate = useNavigate();
  return (
    <div className="flex flex-shrink-0 flex-wrap items-center justify-between gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Verzia je hotová — agent stavby na nej už nepracuje. Na čokoľvek k nej sa opýtaj Poradcu; zmenu z jeho
        odpovede založíš ako novú verziu.
      </p>
      <button
        type="button"
        onClick={() => navigate(`/poradca?verzia=${encodeURIComponent(versionId)}`)}
        className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
      >
        <MessageSquare className="h-3.5 w-3.5" /> Opýtaj sa Poradcu
      </button>
    </div>
  );
}
