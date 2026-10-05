// Hotová verzia v Riadiacom centre (ICCINT-167): namiesto poľa na správu tlačidlo „Opýtaj sa Poradcu".
//
// Na hotovej verzii agent stavby nepracuje — správa by ostala bez odpovede (backend ju odmietne). Otázky
// k projektu patria Poradcovi, ktorý beží vedľa stavby a len číta; zmenu z jeho odpovede založí ako novú
// verziu. Tlačidlo otvorí Poradcu s touto verziou predvolenou.

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
