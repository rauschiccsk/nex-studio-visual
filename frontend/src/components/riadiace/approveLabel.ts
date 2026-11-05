// The SAME `schvalit` verb is the manager's approve/advance at four gates, and the consequence differs at each —
// the button names what it does (SchvalitBar), and so does the step a CI gate holds greyed out (CiGateBar, DEV-308):
//   • navrh          → "Schváliť plán"       → posunie do stavby (Programovanie)
//   • vizual         → "Schváliť vizuál"     → posunie projekt do stavby (Programovanie)   (CR-1)
//   • programovanie  → "Prejsť na overenie"  → posunie na Verifikáciu (overenie Auditorom)
//   • verifikacia    → "Schváliť na Hotovo"  → overená verzia sa označí ako Hotovo (terminálny podpis)
// Any null/legacy state falls back to the plan-approval copy (the earliest gate).
export function approveLabel(stage: string | null | undefined): string {
  if (stage === "verifikacia") return "Schváliť na Hotovo";
  if (stage === "programovanie") return "Prejsť na overenie";
  if (stage === "vizual") return "Schváliť vizuál";
  return "Schváliť plán";
}
