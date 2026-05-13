import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";

// ─── SVG icon helpers ───────────────────────────────────────────────────────

const IconHome = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
  </svg>
);

const IconFolder = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
  </svg>
);

const IconVersions = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2" />
  </svg>
);

const IconWorkflow = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
  </svg>
);

// Embedded agent terminal icons (Director directive 2026-05-13 — replace
// external Windows Terminal tabs with NEX Studio top-level pages).
const IconDesigner = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
  </svg>
);

const IconImplementer = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
  </svg>
);

const IconAuditor = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
  </svg>
);

const IconSpec = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
  </svg>
);

const IconSolution = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
  </svg>
);

const IconSummary = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
  </svg>
);

const IconArchitecture = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
  </svg>
);

const IconAudit = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
  </svg>
);

const IconTaskPlan = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
  </svg>
);

const IconImplementation = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
  </svg>
);

const IconBook = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
  </svg>
);

const IconSettings = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>
);

const IconAdmin = () => (
  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
  </svg>
);

const IconLogout = () => (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
  </svg>
);

const IconSidebarToggle = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <rect x="3" y="3" width="18" height="18" rx="2" strokeWidth="1.8" />
    <path d="M9 3v18" strokeWidth="1.8" />
  </svg>
);

const IconKey = () => (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 7a4 4 0 11-4 4m4-4a4 4 0 00-4-4m4 4l4 4m-8 0l-6 6v3h3l6-6m-3-3l3 3" />
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
}

function NavItem({ icon, label, path, collapsed, active, onClick }: NavItemProps) {
  const navigate = useNavigate();

  const handleClick = () => {
    if (onClick) { onClick(); return; }
    if (path) navigate(path);
  };

  const base = "flex items-center gap-2.5 py-2 rounded-lg text-sm transition-colors w-full";
  const px = collapsed ? "px-0 justify-center" : "px-3";
  const color = active
    ? "bg-primary-500/15 text-primary-400"
    : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200";

  return (
    <button className={`${base} ${px} ${color}`} onClick={handleClick} title={collapsed ? label : undefined}>
      {icon}
      {!collapsed && <span>{label}</span>}
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
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);

  const isActive = (path: string) =>
    path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);

  const isStepActive = (steps: string[]) => {
    if (!selectedProject || !selectedVersion) return false;
    const base = `/projects/${selectedProject.slug}/versions/${selectedVersion.versionId}/`;
    return steps.some((s) => location.pathname === base + s);
  };

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const initials = user?.username ? user.username.slice(0, 1).toUpperCase() : "?";

  // Pipeline step nav data. The "Solution" entry represents the parallel
  // Krok 2 phase — it lands on /profspec (Vývojová dokumentácia) and the
  // SolutionTabs header inside ProfSpecPage + UIDesignPage lets the user
  // switch to /uidesign (Návrh UI dizajnu). Active highlight covers both
  // underlying routes.
  const pipelineSteps: { label: string; step: string; icon: React.ReactNode; matchSteps?: string[] }[] = [
    { label: "Specification", step: "spec", icon: <IconSpec /> },
    { label: "Solution", step: "profspec", icon: <IconSolution />, matchSteps: ["profspec", "uidesign"] },
    { label: "Summary", step: "summary", icon: <IconSummary /> },
    { label: "Architecture", step: "architecture", icon: <IconArchitecture /> },
    { label: "Quality Audit", step: "audit", icon: <IconAudit /> },
    { label: "Task Plan", step: "taskplan", icon: <IconTaskPlan /> },
    { label: "Implementation", step: "implementacia", icon: <IconImplementation /> },
  ];

  const hasProject = Boolean(selectedProject);
  const hasFullContext = Boolean(selectedProject && selectedVersion);

  // Fallback target when no project is pinned yet — sends the user to
  // the project list where the Pin icon explicitly selects a project.
  const fallbackPath = "/projects";

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
        <NavItem icon={<IconHome />} label="Dashboard" path="/" collapsed={collapsed} active={isActive("/")} />
        <NavItem icon={<IconFolder />} label="Projects" path="/projects" collapsed={collapsed} active={isActive("/projects")} />
        <NavItem
          icon={<IconVersions />}
          label="Versions"
          path={hasProject ? `/projects/${selectedProject!.slug}` : fallbackPath}
          collapsed={collapsed}
          active={hasProject ? location.pathname === `/projects/${selectedProject!.slug}` : false}
        />
        <NavItem
          icon={<IconWorkflow />}
          label="Workflow"
          path={
            hasFullContext
              ? `/projects/${selectedProject!.slug}/versions/${selectedVersion!.versionId}`
              : fallbackPath
          }
          collapsed={collapsed}
          active={
            hasFullContext
              ? location.pathname ===
                `/projects/${selectedProject!.slug}/versions/${selectedVersion!.versionId}`
              : false
          }
        />

        {/* Embedded agent terminals — replace external Windows Terminal
            tabs with full-page xterm.js sessions inside NEX Studio
            (Director directive 2026-05-13). */}
        <NavItem icon={<IconDesigner />} label="Designer" path="/designer" collapsed={collapsed} active={isActive("/designer")} />
        <NavItem icon={<IconImplementer />} label="Implementer" path="/implementer" collapsed={collapsed} active={isActive("/implementer")} />
        <NavItem icon={<IconAuditor />} label="Auditor" path="/auditor" collapsed={collapsed} active={isActive("/auditor")} />

        {/* Selected project indicator — shown under the Designer/
            Implementer/Auditor trio. Pin icon → user explicitly chose
            this project in /projects. Version suffix appears once the
            user opens a verzia (auto-set by useActiveContextSync). */}
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

        {pipelineSteps.map((s) => (
          <NavItem
            key={s.step}
            icon={s.icon}
            label={s.label}
            path={
              hasFullContext
                ? `/projects/${selectedProject!.slug}/versions/${selectedVersion!.versionId}/${s.step}`
                : fallbackPath
            }
            collapsed={collapsed}
            active={isStepActive(s.matchSteps ?? [s.step])}
          />
        ))}
        <NavItem icon={<IconBook />} label="Knowledge Base" path="/kb" collapsed={collapsed} active={isActive("/kb")} />
        <NavItem icon={<IconBook />} label="Project Specs" path="/project-specs" collapsed={collapsed} active={isActive("/project-specs")} />
        <NavItem icon={<IconKey />} label="Credentials" path="/credentials" collapsed={collapsed} active={isActive("/credentials")} />

        <SectionLabel label="Settings" collapsed={collapsed} />
        <NavItem icon={<IconSettings />} label="Settings" path="/settings" collapsed={collapsed} active={isActive("/settings")} />

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
              {["Používatelia", "Delegácie", "Execution Logs", "Guardian", "Migrácie"].map((item) => (
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
              <button onClick={handleLogout} title="Sign out" className="shrink-0 text-slate-600 hover:text-slate-400 transition-colors">
                <IconLogout />
              </button>
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
