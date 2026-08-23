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
    // SHOW the backend's own sentence when there is one (ICCINT-22). The first version of this helper
    // replaced it ALWAYS with `reasonFor(status)` and filed the truth under a collapsible — so a refusal
    // that knew exactly what was wrong ("Port 10225 patrí do rezervovaného bloku, ktorý má pridelený
    // nex-payables") reached the Manažér as "zadané údaje nie sú v poriadku". Every screen in the cockpit
    // went through here, so that was not one bad message: it was the app knowing the answer and withholding
    // it, everywhere.
    //
    // The guard the original was reaching for is real — an English or raw detail helps nobody. But the
    // answer to "some backend messages are English" is to TRANSLATE them, not to hide all of them: hidden,
    // they are never fixed either. Surfacing them makes the remaining ones visible, which is how they get
    // corrected. The canned reason stays as the fallback for when there is no sentence at all (a bare 500,
    // a FastAPI validation object).
    // The detail carries ONLY the status once the sentence is in the message: repeating it under
    // "Technický detail" puts the same words on screen twice and makes the collapsible look like it holds
    // something more.
    return {
      message: raw ? `${phrase} — ${raw}` : `${phrase} — ${reasonFor(err.status)}.`,
      detail: `HTTP ${err.status}`,
    };
  }
  if (err instanceof Error && err.message && err.message !== "[object Object]") {
    return { message: `${phrase} — skús to prosím znova.`, detail: err.message };
  }
  return { message: `${phrase} — skús to prosím znova.` };
}
