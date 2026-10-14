// DEV-50 — the work kind of a version (Director 10.10.2026). DEV-75: a fix of our own error is a warranty fix — its
// tasks count as „Oprava chyby“, whose discount in Settings is 100 % unless the manager decides otherwise.
// DEV-118 — Director 14.10.2026: „BUG - oprava, GIFT - Dar, a požiadavka zákazníka.“ A whole piece of work may be a
// gift — its tasks count as „Dar — pozornosť ICC“. The kinds come from the backend's contract; a kind added there
// without its words here fails the type check.

import type { components } from "@/services/api/pipeline.generated";

export type WorkKind = components["schemas"]["WorkKindWrite"]["work_kind"];

const WORDS: Record<WorkKind, { label: string; hint: string }> = {
  change: {
    label: "Zmena alebo nová práca",
    hint: "účtuje sa podľa dodaných tokenov",
  },
  fix: {
    label: "Oprava chyby v dodanom kóde",
    hint: "kód nerobí to, čo hovorí schválená špecifikácia — záručná oprava, predvolene so zľavou 100 %",
  },
  gift: {
    label: "Dar — pozornosť ICC",
    hint: "práca, ktorú ICC dáva zákazníkovi — súpis ukáže jej hodnotu, predvolene so zľavou 100 %",
  },
};

export const WORK_KIND_OPTIONS: { value: WorkKind; label: string; hint: string }[] = (
  Object.keys(WORDS) as WorkKind[]
).map((value) => ({ value, ...WORDS[value] }));
