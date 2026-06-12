import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
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
// accessible name; emoji is decorative. IconSidebarToggle stays SVG —
// it's a UI toggle, not a nav entry.

const Emoji = ({ glyph }: { glyph: string }) => (
  <span aria-hidden="true" className="text-base leading-none shrink-0 w-4 inline-flex items-center justify-center">
    {glyph}
  </span>
);

const IconHome = () => <Emoji glyph="🏠" />;
const IconFolder = () => <Emoji glyph="📁" />;
const IconVersions = () => <Emoji glyph="🌿" />;
const IconBacklog = () => <Emoji glyph="📋" />;
const IconMetrics = () => <Emoji glyph="📊" />;

const IconCoordinator = () => <Emoji glyph="🧭" />;
const IconCockpit = () => <Emoji glyph="🎛️" />;

const IconKbBook = () => <Emoji glyph="📚" />;
const IconProjectSpecsBook = () => <Emoji glyph="📖" />;

const IconSettings = () => <Emoji glyph="⚙️" />;
const IconAdmin = () => <Emoji glyph="🛡️" />;
const IconLogout = () => <Emoji glyph="🚪" />;
const IconKey = () => <Emoji glyph="🔑" />;

// Sidebar toggle stays SVG — it's a UI control (collapse button), not a
// navigation entry, so emoji wouldn't fit visually.
const IconSidebarToggle = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <rect x="3" y="3" width="18" height="18" rx="2" strokeWidth="1.8" />
    <path d="M9 3v18" strokeWidth="1.8" />
  </svg>
);

// ─── NavItem ─────────────────────────────────────────────────────────────────

interface NavItemProps {
  icon: React.ReactNode;
  label: string;
  path?: string;
  collapsed: boolean;
  active?: boolean;
  onClick?: () => void;
  /** When true the link is rendered greyed out and is not clickable.
   *  Used for Workflow + pipeline-step links when no verzia is selected
   *  yet — the link stays visible (discoverability) but cannot navigate
   *  to a fallback page (Director directive 2026-05-14: Workflow shows
   *  workflow content, never project content). */
  disabled?: boolean;
  /** Optional tooltip shown when the item is disabled — explains why
   *  the link is unavailable + how to enable it. */
  disabledTitle?: string;
  /** When true an amber attention dot is shown (e.g. cockpit awaiting
   *  Director — F-007 §9 in-app notification). */
  badge?: boolean;
}

function NavItem({
  icon,
  label,
  path,
  collapsed,
  active,
  onClick,
  disabled,
  disabledTitle,
  badge,
}: NavItemProps) {
  const navigate = useNavigate();

  const handleClick = () => {
    if (disabled) return;
    if (onClick) { onClick(); return; }
    if (path) navigate(path);
  };

  const base = "flex items-center gap-2.5 py-2 rounded-lg text-sm transition-colors w-full";
  const px = collapsed ? "px-0 justify-center" : "px-3";
  const color = disabled
    ? "text-slate-600 opacity-40 cursor-not-allowed"
    : active
      ? "bg-primary-500/15 text-primary-400"
      : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200";

  const tooltip = disabled
    ? disabledTitle ?? label
    : collapsed
      ? label
      : undefined;

  return (
    <button
      className={`${base} ${px} ${color} relative`}
      onClick={handleClick}
      disabled={disabled}
      title={tooltip}
    >
      {icon}
      {!collapsed && <span>{label}</span>}
      {badge && (
        <span
          aria-label="čaká na Director-a"
          className={
            collapsed
              ? "absolute right-1 top-1 h-2 w-2 rounded-full bg-amber-400"
              : "ml-auto h-2 w-2 rounded-full bg-amber-400"
          }
        />
      )}
    </button>
  );
}

// ─── SectionLabel ────────────────────────────────────────────────────────────

function SectionLabel({ label, collapsed }: { label: string; collapsed: boolean }) {
  if (collapsed) return <div className="h-3" />;
  return (
    <div className="pt-3 pb-1 px-3 text-[10px] text-slate-700 uppercase tracking-widest font-semibold">
      {label}
    </div>
  );
}

// ─── Sidebar ─────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
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

  return (
    <aside
      className="flex-shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col select-none transition-all duration-200 overflow-x-hidden"
      style={{ width: collapsed ? "3.5rem" : "14rem" }}
    >
      {/* Logo + toggle */}
      <div className="px-3 py-3 border-b border-slate-800 flex items-center gap-3 min-h-[56px]">
        {!collapsed && (
          <>
            <div className="w-8 h-8 rounded-lg bg-primary-600 flex items-center justify-center text-white font-black text-sm shrink-0">
              NS
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-bold text-slate-100 leading-tight">NEX Studio</div>
              <div className="text-[10px] text-primary-400 font-mono">
                v{import.meta.env.VITE_APP_VERSION || "dev"}
              </div>
            </div>
          </>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className={`flex items-center justify-center rounded hover:bg-slate-800 text-slate-500 hover:text-slate-300 transition-colors shrink-0 ${collapsed ? "w-8 h-8" : "w-6 h-6"}`}
          title={collapsed ? "Rozšíriť" : "Zúžiť"}
        >
          <IconSidebarToggle />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto overflow-x-hidden">
        <NavItem icon={<IconHome />} label="Prehľad" path="/" collapsed={collapsed} active={isActive("/")} />
        <NavItem icon={<IconFolder />} label="Projekty" path="/projects" collapsed={collapsed} active={isActive("/projects")} />

        {/* Selected project indicator — placed directly under Projects
            (Director directive 2026-05-15: belongs near the source of
            the Pin action). Pin icon → user explicitly chose this
            project in /projects. Version suffix appears once the user
            opens a verzia (auto-set by useActiveContextSync). */}
        {hasProject && !collapsed && (
          <div className="px-3 pb-1 flex items-center gap-1.5 text-[10px] text-slate-500 font-mono">
            <svg className="w-3 h-3 shrink-0 text-primary-400" fill="currentColor" viewBox="0 0 24 24">
              <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" />
            </svg>
            <span className="truncate flex-1">
              {selectedProject!.name}
              {selectedVersion && (
                <span className="text-slate-600"> · {selectedVersion.versionNumber}</span>
              )}
            </span>
            <button
              onClick={() => setSelectedProject(null)}
              title="Zrušiť výber projektu"
              className="text-slate-600 hover:text-slate-300 shrink-0"
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
          path={hasProject ? `/projects/${selectedProject!.slug}` : projectsFallback}
          collapsed={collapsed}
          active={hasProject ? location.pathname === `/projects/${selectedProject!.slug}` : false}
        />
        {/* E2 (CR-NS-041): per-project Backlog. Project-scoped — disabled (not cross-domain fallback)
            when no project is selected. */}
        <NavItem
          icon={<IconBacklog />}
          label="Zásobník"
          path={hasProject ? `/projects/${selectedProject!.slug}/backlog` : projectsFallback}
          collapsed={collapsed}
          active={hasProject ? isActive(`/projects/${selectedProject!.slug}/backlog`) : false}
          disabled={!hasProject}
          disabledTitle="Vyber projekt pre prístup k zásobníku"
        />
        {/* E5 (CR-NS-044): per-project metrics / ROI. Project-scoped — disabled (not cross-domain
            fallback) when no project is selected. */}
        <NavItem
          icon={<IconMetrics />}
          label="Metriky"
          path={hasProject ? `/projects/${selectedProject!.slug}/metrics` : projectsFallback}
          collapsed={collapsed}
          active={hasProject ? isActive(`/projects/${selectedProject!.slug}/metrics`) : false}
          disabled={!hasProject}
          disabledTitle="Vyber projekt pre prístup k metrikám"
        />
        {/* E3(a) (CR-NS-039): hub-and-spoke — the Coordinator is the Director's single ad-hoc
            consult terminal (has READ docs/specs + schemas, CR-033). The Designer / Customer /
            Implementer / Auditor sidebar terminals were removed; the pipeline still dispatches all
            roles internally. */}
        <NavItem icon={<IconCoordinator />} label="AG Koordinátor" path="/coordinator" collapsed={collapsed} active={isActive("/coordinator")} />
        <NavItem icon={<IconCockpit />} label="Orchestrácia" path="/cockpit" collapsed={collapsed} active={isActive("/cockpit")} badge={cockpitAwaiting} />

        <NavItem icon={<IconKbBook />} label="Dokumentácia" path="/kb" collapsed={collapsed} active={isActive("/kb")} />
        <NavItem icon={<IconProjectSpecsBook />} label="Špecifikácie" path="/project-specs" collapsed={collapsed} active={isActive("/project-specs")} />
        {/* E4 (CR-NS-046): Credentials nav gated to Ri (mirrors the backend JWT-ri restriction + the
            presence toggle gate); nav visibility only. */}
        {user?.role === "ri" && (
          <NavItem icon={<IconKey />} label="Prístupy" path="/credentials" collapsed={collapsed} active={isActive("/credentials")} />
        )}

        <SectionLabel label="Nastavenia" collapsed={collapsed} />
        <NavItem icon={<IconSettings />} label="Nastavenia" path="/settings" collapsed={collapsed} active={isActive("/settings")} />

        {/* Admin */}
        <div className="pt-2">
          <button
            onClick={() => setAdminOpen((o) => !o)}
            className={`flex items-center gap-2.5 py-2 rounded-lg text-sm text-slate-500 hover:bg-slate-800/60 hover:text-slate-400 transition-colors w-full ${collapsed ? "px-0 justify-center" : "px-3"}`}
            title={collapsed ? "Admin" : undefined}
          >
            <IconAdmin />
            {!collapsed && (
              <>
                <span>Admin</span>
                <svg
                  className={`w-3 h-3 ml-auto transition-transform ${adminOpen ? "rotate-90" : ""}`}
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </>
            )}
          </button>
          {adminOpen && !collapsed && (
            <div className="pl-6 mt-0.5 space-y-0.5">
              {["Používatelia", "Delegácie", "Protokoly vykonávania", "Guardian", "Migrácie"].map((item) => (
                <button
                  key={item}
                  className="block w-full text-left px-3 py-1.5 rounded text-xs text-slate-500 hover:bg-slate-800/60 hover:text-slate-400 transition-colors"
                >
                  {item}
                </button>
              ))}
            </div>
          )}
        </div>
      </nav>

      {/* User */}
      <div className="p-3 border-t border-slate-800">
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
                ? "bg-amber-500/10 text-amber-300 hover:bg-amber-500/20"
                : "text-slate-400 hover:bg-slate-800/60"
            } ${collapsed ? "justify-center" : ""}`}
          >
            <span className="text-sm leading-none">{isAway ? "🌙" : "🟢"}</span>
            {!collapsed && <span>{isAway ? "Preč" : "Pri počítači"}</span>}
          </button>
        )}
        <div className={`flex items-center gap-2.5 px-2 py-1.5 rounded-lg ${collapsed ? "justify-center" : ""}`}>
          <div className="w-7 h-7 rounded-full bg-primary-600 flex items-center justify-center text-xs font-bold shrink-0">
            {initials}
          </div>
          {!collapsed && (
            <>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-slate-200 truncate">{user?.username ?? "—"}</div>
                <div className="text-[10px] text-slate-500">Director · Ri</div>
              </div>
              <button onClick={handleLogout} title="Odhlásiť sa" className="shrink-0 text-slate-600 hover:text-slate-400 transition-colors">
                <IconLogout />
              </button>
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
