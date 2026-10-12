// DEV-50 — the work kind of a version (Director 10.10.2026). DEV-75: a fix of our own error is a warranty fix — its
// tasks count as „Oprava chyby“, whose discount in Settings is 100 % unless the manager decides otherwise.

export type WorkKind = "fix" | "change";

export const WORK_KIND_OPTIONS: { value: WorkKind; label: string; hint: string }[] = [
  {
    value: "change",
    label: "Zmena alebo nová práca",
    hint: "účtuje sa podľa dodaných tokenov",
  },
  {
    value: "fix",
    label: "Oprava chyby v dodanom kóde",
    hint: "kód nerobí to, čo hovorí schválená špecifikácia — záručná oprava, predvolene so zľavou 100 %",
  },
];
