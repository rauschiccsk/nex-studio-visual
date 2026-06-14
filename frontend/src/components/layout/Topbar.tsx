import { useLocation } from "react-router-dom";
import { Header, ThemeToggle } from "nex-shared";
import { useTheme } from "@/contexts/ThemeContext";

const breadcrumbMap: Record<string, string> = {
  "/": "Prehľad",
  "/projects": "Projekty",
  "/kb": "Dokumentácia",
  "/settings": "Nastavenia",
};

export default function Topbar() {
  const location = useLocation();
  // E1 chrome unification (CR-NS-067): the theme toggle now lives top-right in the
  // header (the NEX Inbox vzor), wired to the existing per-user ThemeContext.
  const { isDark, toggleDark } = useTheme();

  const label = breadcrumbMap[location.pathname] ?? "NEX Studio";

  // The header chrome (height, bg, border) comes from the shared <Header>; the
  // connection dot + breadcrumb ride the `left` slot, the theme toggle the `right`.
  // Token-driven colors so the chrome renders correctly in both light & dark.
  return (
    <Header
      left={
        <>
          {/* Connected indicator */}
          <div className="flex items-center gap-1.5 shrink-0">
            <div className="w-2 h-2 rounded-full bg-[var(--color-status-success)]" />
            <span className="text-xs text-[var(--color-text-secondary)] font-medium">Pripojené</span>
          </div>

          {/* Breadcrumb */}
          <div className="flex items-center gap-1.5 flex-1 min-w-0 overflow-hidden text-xs text-[var(--color-text-muted)]">
            <span className="text-[var(--color-text-muted)]">/</span>
            <span className="text-[var(--color-text-secondary)]">{label}</span>
          </div>
        </>
      }
      right={<ThemeToggle theme={isDark ? "dark" : "light"} onToggle={toggleDark} />}
    />
  );
}
