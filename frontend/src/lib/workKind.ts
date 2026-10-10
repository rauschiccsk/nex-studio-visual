// DEV-50 — the work kind of a version: a fix of our own error is never billed (Director 10.10.2026).

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
    hint: "kód nerobí to, čo hovorí schválená špecifikácia — neúčtuje sa",
  },
];
