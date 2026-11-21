import type { ReactNode } from "react";

/**
 * DEV-485 — the cockpit's own sections under a panel of nex-shared's settings kit, padded like that panel (its root is
 * `p-6`), so their text keeps the page's margin. 21.11.2026 „Zodpovedný v Plane“, „Otázky Staviteľa najprv s
 * Poradcom“ and „Farba konta“ stood flush against the sidebar, their selects reaching the right edge (the layout
 * guard's rule 6 keeps it so).
 */
export default function SettingsExtraSections({ children }: { children: ReactNode }) {
  return <div className="px-6 pb-6">{children}</div>;
}
