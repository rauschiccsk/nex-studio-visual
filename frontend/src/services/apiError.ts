// Plain-language framing for API/action errors (self-sufficiency kernel, audit Theme 2).
//
// Across the operate surfaces (Prístupy, Zákazníci, Nasadenie, Nový projekt) a failed request either fell
// through SILENTLY or surfaced a raw "… (HTTP 500)" / an English backend `detail` — meaningless to a
// non-expert, with no next step. This turns any thrown error into a plain-Slovak sentence, keeping the raw
// technical text available separately (for a collapsible "Technický detail", mirroring the build-failure
// framing). The caller passes a gender-correct action phrase (e.g. "Uloženie zlyhalo", "Akceptácia zlyhala")
// so the message reads naturally; the helper appends the humanised reason.

import { ApiError } from "@/services/api";

export interface HumanError {
  /** The plain-Slovak, manager-facing sentence. */
  message: string;
  /** The raw technical text (HTTP status + backend detail), for an optional collapsible. Absent when unknown. */
  detail?: string;
}

// Plain-Slovak reason clause per HTTP status (no trailing period — the composed sentence adds structure).
function reasonFor(status: number): string {
  if (status >= 500) return "chyba na strane servera — skús to o chvíľu znova";
  switch (status) {
    case 400:
      return "zadané údaje nie sú v poriadku";
    case 401:
      return "prihlásenie vypršalo — prihlás sa znova";
    case 403:
      return "na túto akciu nemáš oprávnenie";
    case 404:
      return "položka sa nenašla (možno ju medzitým niekto zmenil)";
    case 409:
      return "položka sa medzičasom zmenila alebo už existuje";
    case 422:
      return "zadané údaje nie sú v poriadku";
    default:
      return "skús to prosím znova";
  }
}

/**
 * Turn a thrown error into a plain-Slovak {message, detail}.
 * @param err   the caught error (ApiError or anything)
 * @param phrase a gender-correct Slovak action phrase, e.g. "Uloženie zlyhalo" / "Akceptácia zlyhala"
 */
export function humanizeApiError(err: unknown, phrase: string): HumanError {
  if (err instanceof ApiError) {
    // The lib parses the FastAPI {detail}; a non-string/object detail can render as "[object Object]" — never
    // show that. Keep a clean raw detail only when it's a meaningful string.
    const raw = typeof err.message === "string" && err.message && err.message !== "[object Object]" ? err.message : "";
    return {
      message: `${phrase} — ${reasonFor(err.status)}.`,
      detail: raw ? `HTTP ${err.status}: ${raw}` : `HTTP ${err.status}`,
    };
  }
  if (err instanceof Error && err.message && err.message !== "[object Object]") {
    return { message: `${phrase} — skús to prosím znova.`, detail: err.message };
  }
  return { message: `${phrase} — skús to prosím znova.` };
}
