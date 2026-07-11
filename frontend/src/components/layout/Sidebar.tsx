import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Sidebar as ShellSidebar, NavItem, SectionLabel, Brand, UserCard, NavIcon } from "nex-shared";
import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { usePresenceStore } from "@/store/usePresenceStore";
import { usePipelineWs } from "@/hooks/usePipelineWs";
import type { UserRole } from "@/types/user";

// UserCard subtitle (CR-NS-093): derive the "<Title> · <Code>" label from the
// logged-in user's role. Studio provisions all three roles (ri/ha/shu) — titles
// match SettingsPage ROLE_OPTIONS (ri → Manažér, ha → Medior, shu → Junior).
// CR-V2-004: operator label Director → Manažér (the ``ri`` ACCESS-ROLE token is
// unchanged — auth gate stays per design §8; this is a display relabel only).
const ROLE_LABEL: Record<UserRole, string> = {
  ri: "Manažér",
  ha: "Medior",
  shu: "Junior",
};

// ─── Icon helpers ───────────────────────────────────────────────────────────
// Director directive 2026-05-15: full Unicode emoji glyphs instead of
// monochrome SVG line-art. Browser renders these via the system emoji
// font (Segoe UI Emoji on Windows, Apple Color Emoji on macOS, Noto
// Color Emoji on Linux/ANDROS) so they appear fully colored without
// any CSS class. ``aria-hidden`` because the NavItem text label is the
// accessible name; emoji is decorative.

// Colored nav glyphs via the shared <NavIcon> (E1 chrome unification, CR-NS-067).
// FINAL v2.0.0 sidebar glyphs (CR-V2-019, design §4.1).
const IconHome = () => <NavIcon glyph="🏠" />;
const IconGuide = () => <NavIcon glyph="🧭" />;
const IconFolder = () => <NavIcon glyph="📁" />;
const IconVersions = () => <NavIcon glyph="🌿" />;
const IconBacklog = () => <NavIcon glyph="📋" />;
const IconMetrics = () => <NavIcon glyph="📊" />;

// v2 spine STEP 1 (Chrbtica): the Riadiace centrum = the ONE conversation-centred
// build surface (replaces the AI Agent terminal + the 4-phase Vývoj board);
// Špecifikácia = the read-only spec shell.
const IconRiadiace = () => <NavIcon glyph="🎛️" />;
const IconSpec = () => <NavIcon glyph="📄" />;

// v2 per-customer deploy surfaces (pages land in Milestone G — CR-V2-025/027).
const IconCustomers = () => <NavIcon glyph="👥" />;
const IconUat = () => <NavIcon glyph="🧪" />;
const IconProd = () => <NavIcon glyph="🚀" />;

const IconKbBook = () => <NavIcon glyph="📚" />;

const IconUpdates = () => <NavIcon glyph="✨" />;
const IconSettings = () => <NavIcon glyph="⚙️" />;
const IconKey = () => <NavIcon glyph="🔑" />;

// ─── Sidebar ─────────────────────────────────────────────────────────────────
// Thin composition over the shared <Sidebar> shell (E1 Phase B2, CR-NS-049):
// the generic frame / nav primitives live in nex-shared; ALL NEX-Studio-specific
// behavior stays here — routes, active detection (useLocation), the selected
// project/version indicator, project-scoped disabled items, the cockpit awaiting
// badge, the admin submenu, the E6 presence toggle, Credentials ri-gating, and
// the user footer. Collapse state is owned here and passed to the shell.

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const isAway = usePresenceStore((s) => s.isAway);
  const setIsAway = usePresenceStore((s) => s.setIsAway);
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);

  // Sidebar-level pipeline WS on the pinned version (F-007 §7). Doubles as the
  // §9 Manažér-presence signal; drives the cockpit "awaiting" attention dot.
  const { board: pipelineBoard } = usePipelineWs(selectedVersion?.versionId ?? null);
  const cockpitAwaiting = pipelineBoard?.state?.status === "awaiting_manazer";

  const isActive = (path: string) =>
    path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  // CR-NS-089: show the logged-in user's full name (first + last), falling back
  // to username → email. Initials derive from the same resolved source.
  const displaySource =
    [user?.first_name, user?.last_name].filter(Boolean).join(" ") ||
    user?.username ||
    user?.email ||
    "";
  const displayName = displaySource || "—";
  const initials = displaySource ? displaySource.slice(0, 1).toUpperCase() : "?";

  const hasProject = Boolean(selectedProject);
  const projectsFallback = "/projects";

  // ─── Logo slot (shared Brand — E1 chrome unification, CR-NS-067) ───────────
  const logo = (
    <Brand initials="NS" name="NEX Studio" version={`v${import.meta.env.VITE_APP_VERSION || "dev"}`} />
  );

  // ─── Footer slot ─────────────────────────────────────────────────────────
  const footer = (
    <>
      {/* E6 (CR-NS-038): Manažér-only Telegram presence toggle. "Preč" → agent-needs-Manažér
          events ping Telegram even with the cockpit open. Collapsed sidebar → icon only. */}
      {user?.role === "ri" && (
        <button
          onClick={() => setIsAway(!isAway)}
          title={
            isAway
              ? "Preč — upozornenia na Telegram zapnuté aj s otvoreným Riadiacim centrom. Klikni pre „Pri počítači“."
              : "Pri počítači — bez Telegram upozornení (vidíš Riadiace centrum). Klikni pred odchodom od počítača."
          }
          className={`flex items-center gap-2 w-full rounded-lg px-2 py-1.5 mb-1 text-xs transition-colors ${
            isAway
              ? "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
              : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
          } ${collapsed ? "justify-center" : ""}`}
        >
          <span className="text-sm leading-none">{isAway ? "🌙" : "🟢"}</span>
          {!collapsed && <span>{isAway ? "Preč" : "Pri počítači"}</span>}
        </button>
      )}
      <UserCard
        initials={initials}
        name={displayName}
        subtitle={user?.role ? ROLE_LABEL[user.role] : "—"}
        onLogout={handleLogout}
      />
    </>
  );

  return (
    // FINAL v2.0.0 navigation (CR-V2-019, design §4.1). Order top-to-bottom:
    //   Prehľad · Projekty[📌 pin] · Verzie · Zásobník · AI Agent · Vývoj ·
    //   Zákazníci · UAT · PROD · Metriky · Dokumentácia · Prístupy ·
    //   Aktualizácie · Nastavenia.  Footer: presence 🟢/🌙 + user card.
    //
    // AUD-7 — INVARIANT (do NOT violate): there is intentionally NO Auditor /
    // Audítor nav item. The Auditor's verdict and findings are reachable ONLY
    // via Vývoj → Verifikácia (CR-V2-021). A future edit must never add an
    // Auditor entry here — the verifier is independent and surfaces inside the
    // build board, not as a standalone destination (design §4.1, §2.4).
    <ShellSidebar
      collapsed={collapsed}
      onToggleCollapse={() => setCollapsed((c) => !c)}
      logo={logo}
      footer={footer}
    >
      <NavItem icon={<IconHome />} label="Prehľad" active={isActive("/")} onClick={() => navigate("/")} />
      {/* Getting-started guide for a non-expert operator (handover). Always accessible (not project-scoped). */}
      <NavItem icon={<IconGuide />} label="Ako začať" active={isActive("/getting-started")} onClick={() => navigate("/getting-started")} />
      <NavItem icon={<IconFolder />} label="Projekty" active={isActive("/projects")} onClick={() => navigate("/projects")} />

      {/* Selected project indicator — placed directly under Projects
          (Director directive 2026-05-15: belongs near the source of
          the Pin action). Pin icon → user explicitly chose this
          project in /projects. Version suffix appears once the user
          opens a verzia (auto-set by useActiveContextSync). */}
      {hasProject && !collapsed && (
        <div className="px-3 pb-1 flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)] font-mono">
          <svg className="w-3 h-3 shrink-0 text-primary-400" fill="currentColor" viewBox="0 0 24 24">
            <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" />
          </svg>
          <span className="truncate flex-1">
            {selectedProject!.name}
            {selectedVersion && (
              <span className="text-[var(--color-text-muted)]"> · {selectedVersion.versionNumber}</span>
            )}
          </span>
          <button
            onClick={() => setSelectedProject(null)}
            title="Zrušiť výber projektu"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] shrink-0"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      <NavItem
        icon={<IconVersions />}
        label="Verzie"
        active={hasProject ? location.pathname === `/projects/${selectedProject!.slug}` : false}
        onClick={() => navigate(hasProject ? `/projects/${selectedProject!.slug}` : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k verziám"
      />
      {/* E2 (CR-NS-041): per-project Backlog. Project-scoped — disabled (not cross-domain fallback)
          when no project is selected. */}
      <NavItem
        icon={<IconBacklog />}
        label="Zásobník"
        active={hasProject ? isActive(`/projects/${selectedProject!.slug}/backlog`) : false}
        onClick={() => navigate(hasProject ? `/projects/${selectedProject!.slug}/backlog` : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k zásobníku"
      />
      {/* v2 spine STEP 1 (Chrbtica): the Riadiace centrum — the ONE conversation-centred build
          surface that replaces the AI Agent terminal + the 4-phase Vývoj board. Project- +
          version-scoped per design §4.1 — disabled when no project is pinned. The "čaká na Manažéra"
          attention dot moves here (it fired on Vývoj before). */}
      <NavItem
        icon={<IconRiadiace />}
        label="Riadiace centrum"
        active={hasProject ? isActive("/riadiace-centrum") : false}
        onClick={() => navigate(hasProject ? "/riadiace-centrum" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k Riadiacemu centru"
        badge={hasProject && cockpitAwaiting}
        badgeLabel="čaká na Manažéra"
      />
      {/* v2 spine STEP 1: Špecifikácia — the read-only spec shell (the agreed .md is wired in a
          later step). Project-scoped — disabled (not hidden) when no project is pinned. */}
      <NavItem
        icon={<IconSpec />}
        label="Dokumenty"
        active={hasProject ? isActive("/specifikacia") : false}
        onClick={() => navigate(hasProject ? "/specifikacia" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k dokumentom"
      />
      {/* v2 (CR-V2-019): per-customer deploy surfaces (design §3 / §4.1). Nav items are added now;
          their PAGES land in Milestone G (Zákazníci = CR-V2-025, UAT/PROD = CR-V2-027). Until then
          the routes resolve to a lightweight "pripravuje sa" placeholder (App.tsx) so they never
          404; project-scoped → disabled when no pin. */}
      <NavItem
        icon={<IconCustomers />}
        label="Zákazníci"
        active={hasProject ? isActive("/zakaznici") : false}
        onClick={() => navigate(hasProject ? "/zakaznici" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k Zákazníkom"
      />
      <NavItem
        icon={<IconUat />}
        label="UAT"
        active={hasProject ? isActive("/uat") : false}
        onClick={() => navigate(hasProject ? "/uat" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k UAT"
      />
      <NavItem
        icon={<IconProd />}
        label="PROD"
        active={hasProject ? isActive("/prod") : false}
        onClick={() => navigate(hasProject ? "/prod" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k PROD"
      />
      {/* E5 (CR-NS-044): per-project metrics / ROI. Project-scoped — disabled (not cross-domain
          fallback) when no project is selected. */}
      <NavItem
        icon={<IconMetrics />}
        label="Metriky"
        active={hasProject ? isActive(`/projects/${selectedProject!.slug}/metrics`) : false}
        onClick={() => navigate(hasProject ? `/projects/${selectedProject!.slug}/metrics` : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k metrikám"
      />

      <NavItem icon={<IconKbBook />} label="Dokumentácia" active={isActive("/kb")} onClick={() => navigate("/kb")} />
      {/* E4 (CR-NS-046): Credentials nav gated to Ri (mirrors the backend JWT-ri restriction + the
          presence toggle gate); nav visibility only. */}
      {user?.role === "ri" && (
        <NavItem icon={<IconKey />} label="Prístupy" active={isActive("/credentials")} onClick={() => navigate("/credentials")} />
      )}

      {/* "Aktualizácie" — user-facing changelog. Sits as the last item of the
          group directly ABOVE the Settings section header (Director directive). */}
      <NavItem icon={<IconUpdates />} label="Aktualizácie" active={isActive("/updates")} onClick={() => navigate("/updates")} />

      <SectionLabel label="Nastavenia" />
      <NavItem icon={<IconSettings />} label="Nastavenia" active={isActive("/settings")} onClick={() => navigate("/settings")} />
    </ShellSidebar>
  );
}
