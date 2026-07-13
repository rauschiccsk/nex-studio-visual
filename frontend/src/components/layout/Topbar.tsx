import { useLocation } from "react-router-dom";
import { Header, ThemeToggle } from "nex-shared";
import { useTheme } from "@/contexts/ThemeContext";

// Audit Theme 6: the breadcrumb names EVERY page (was only 4 → most read the generic "NEX Studio").
const breadcrumbMap: Record<string, string> = {
  "/": "Prehľad",
  "/projects": "Projekty",
  "/projects/new": "Nový projekt",
  "/riadiace-centrum": "Riadiace centrum",
  "/specifikacia": "Dokumenty",
  "/zakaznici": "Zákazníci",
  "/uat": "UAT",
  "/prod": "PROD",
  "/credentials": "Prístupy",
  "/updates": "Aktualizácie",
  "/kb": "Dokumentácia",
  "/settings": "Nastavenia",
};

function breadcrumbFor(pathname: string): string {
  if (breadcrumbMap[pathname]) return breadcrumbMap[pathname];
  // Dynamic project-scoped paths (/projects/<slug>[/versions/<id>|/backlog|/metrics]).
  if (pathname.startsWith("/projects/")) return "Projekt";
  return "NEX Studio Visual";
}

// Plain-Slovak explanation for the kept-abbreviation breadcrumbs (UAT/PROD), shown as a hover tooltip so a
// non-expert manager understands what the abbreviation means.
const breadcrumbTooltips: Record<string, string> = {
  "/uat": "Testovacie prostredie u zákazníka",
  "/prod": "Ostrá prevádzka",
};

export default function Topbar() {
  const location = useLocation();
  // E1 chrome unification (CR-NS-067): the theme toggle lives top-right in the header (the NEX Inbox vzor),
  // wired to the existing per-user ThemeContext.
  const { isDark, toggleDark } = useTheme();

  const label = breadcrumbFor(location.pathname);
  const labelTooltip = breadcrumbTooltips[location.pathname];

  // Audit Theme 6 (honesty): the old hardcoded green "Pripojené" dot was static markup — it never reflected
  // real connection state, so it lied when the backend/WS was down (the kernel-forbidden fake-green). Removed;
  // the Riadiace centrum shows the REAL connection state ("Spojenie stratené — obnovujem…") where it matters.
  return (
    <Header
      left={
        <div className="flex items-center gap-1.5 flex-1 min-w-0 overflow-hidden text-xs text-[var(--color-text-muted)]">
          <span className="text-[var(--color-text-muted)]">/</span>
          <span className="text-[var(--color-text-secondary)]" title={labelTooltip}>
            {label}
          </span>
        </div>
      }
      right={<ThemeToggle theme={isDark ? "dark" : "light"} onToggle={toggleDark} />}
    />
  );
}
