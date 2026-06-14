import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Sidebar as ShellSidebar, NavItem, SectionLabel, Brand, UserCard, NavIcon } from "nex-shared";
import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { usePresenceStore } from "@/store/usePresenceStore";
import { usePipelineWs } from "@/hooks/usePipelineWs";

// ─── Icon helpers ───────────────────────────────────────────────────────────
// Director directive 2026-05-15: full Unicode emoji glyphs instead of
// monochrome SVG line-art. Browser renders these via the system emoji
// font (Segoe UI Emoji on Windows, Apple Color Emoji on macOS, Noto
// Color Emoji on Linux/ANDROS) so they appear fully colored without
// any CSS class. ``aria-hidden`` because the NavItem text label is the
// accessible name; emoji is decorative.

// Colored nav glyphs via the shared <NavIcon> (E1 chrome unification, CR-NS-067).
const IconHome = () => <NavIcon glyph="🏠" />;
const IconFolder = () => <NavIcon glyph="📁" />;
const IconVersions = () => <NavIcon glyph="🌿" />;
const IconBacklog = () => <NavIcon glyph="📋" />;
const IconMetrics = () => <NavIcon glyph="📊" />;

const IconCoordinator = () => <NavIcon glyph="🧭" />;
const IconCockpit = () => <NavIcon glyph="🎛️" />;

const IconKbBook = () => <NavIcon glyph="📚" />;
const IconProjectSpecsBook = () => <NavIcon glyph="📖" />;

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
  // §9 Director-presence signal; drives the cockpit "awaiting" attention dot.
  const { board: pipelineBoard } = usePipelineWs(selectedVersion?.versionId ?? null);
  const cockpitAwaiting = pipelineBoard?.state?.status === "awaiting_director";

  const isActive = (path: string) =>
    path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const initials = user?.username ? user.username.slice(0, 1).toUpperCase() : "?";

  const hasProject = Boolean(selectedProject);
  const projectsFallback = "/projects";

  // ─── Logo slot (shared Brand — E1 chrome unification, CR-NS-067) ───────────
  const logo = (
    <Brand initials="NS" name="NEX Studio" version={`v${import.meta.env.VITE_APP_VERSION || "dev"}`} />
  );

  // ─── Footer slot ─────────────────────────────────────────────────────────
  const footer = (
    <>
      {/* E6 (CR-NS-038): Director-only Telegram presence toggle. "Preč" → agent-needs-Director
          events ping Telegram even with the cockpit open. Collapsed sidebar → icon only. */}
      {user?.role === "ri" && (
        <button
          onClick={() => setIsAway(!isAway)}
          title={
            isAway
              ? "Preč — upozornenia na Telegram zapnuté aj s otvoreným cockpitom. Klikni pre „Pri počítači“."
              : "Pri počítači — bez Telegram upozornení (vidíš cockpit). Klikni pred odchodom od počítača."
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
        name={user?.username ?? "—"}
        subtitle="Director · Ri"
        onLogout={handleLogout}
      />
    </>
  );

  return (
    <ShellSidebar
      collapsed={collapsed}
      onToggleCollapse={() => setCollapsed((c) => !c)}
      logo={logo}
      footer={footer}
    >
      <NavItem icon={<IconHome />} label="Prehľad" active={isActive("/")} onClick={() => navigate("/")} />
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
      {/* E3(a) (CR-NS-039): hub-and-spoke — the Coordinator is the Director's single ad-hoc
          consult terminal (has READ docs/specs + schemas, CR-033). The Designer / Customer /
          Implementer / Auditor sidebar terminals were removed; the pipeline still dispatches all
          roles internally. */}
      <NavItem icon={<IconCoordinator />} label="AG Koordinátor" active={isActive("/coordinator")} onClick={() => navigate("/coordinator")} />
      <NavItem icon={<IconCockpit />} label="Orchestrácia" active={isActive("/cockpit")} onClick={() => navigate("/cockpit")} badge={cockpitAwaiting} badgeLabel="čaká na Director-a" />

      <NavItem icon={<IconKbBook />} label="Dokumentácia" active={isActive("/kb")} onClick={() => navigate("/kb")} />
      <NavItem icon={<IconProjectSpecsBook />} label="Špecifikácie" active={isActive("/project-specs")} onClick={() => navigate("/project-specs")} />
      {/* E4 (CR-NS-046): Credentials nav gated to Ri (mirrors the backend JWT-ri restriction + the
          presence toggle gate); nav visibility only. */}
      {user?.role === "ri" && (
        <NavItem icon={<IconKey />} label="Prístupy" active={isActive("/credentials")} onClick={() => navigate("/credentials")} />
      )}

      <SectionLabel label="Nastavenia" />
      <NavItem icon={<IconSettings />} label="Nastavenia" active={isActive("/settings")} onClick={() => navigate("/settings")} />
    </ShellSidebar>
  );
}
