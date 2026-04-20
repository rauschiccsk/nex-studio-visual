import { useState } from "react";
import { NavLink, useMatch } from "react-router-dom";
import {
  Brain,
  ChevronDown,
  ChevronRight,
  FolderKanban,
  LayoutDashboard,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldCheck,
  Tag,
} from "lucide-react";

import SidebarFooter from "./SidebarFooter";

type NavItem = {
  to: string;
  label: string;
  end?: boolean;
};

type NavGroup = {
  heading: string;
  items: NavItem[];
};

const PRIMARY_NAV: (NavItem & { icon: React.ReactNode })[] = [
  { to: "/", label: "Dashboard", end: true, icon: <LayoutDashboard className="h-4 w-4 shrink-0" aria-hidden="true" /> },
  { to: "/projects", label: "Projects", icon: <FolderKanban className="h-4 w-4 shrink-0" aria-hidden="true" /> },
];

const ADMIN_NAV: NavGroup[] = [
  {
    heading: "Projects",
    items: [
      { to: "/admin/projects", label: "Projects" },
      { to: "/admin/project-modules", label: "Project Modules" },
      { to: "/admin/module-dependencies", label: "Module Dependencies" },
    ],
  },
  {
    heading: "Specifications",
    items: [
      { to: "/admin/raw-specifications", label: "Raw Specifications" },
      { to: "/admin/professional-specifications", label: "Professional Specifications" },
      { to: "/admin/design-documents", label: "Design Documents" },
    ],
  },
  {
    heading: "Architect",
    items: [
      { to: "/admin/architect-sessions", label: "Architect Sessions" },
      { to: "/admin/architect-messages", label: "Architect Messages" },
    ],
  },
  {
    heading: "Work Items",
    items: [
      { to: "/admin/epics", label: "Epics" },
      { to: "/admin/feats", label: "Feats" },
      { to: "/admin/tasks", label: "Tasks" },
    ],
  },
  {
    heading: "Bugs",
    items: [
      { to: "/admin/bugs", label: "Bugs" },
      { to: "/admin/bug-fix-tasks", label: "Bug Fix Tasks" },
      { to: "/admin/auto-fix-attempts", label: "Auto-Fix Attempts" },
    ],
  },
  {
    heading: "Delegation",
    items: [
      { to: "/admin/delegations", label: "Delegations" },
      { to: "/admin/execution-logs", label: "Execution Logs" },
    ],
  },
  {
    heading: "Guardian",
    items: [
      { to: "/admin/guardian-precedents", label: "Guardian Precedents" },
      { to: "/admin/guardian-reviews", label: "Guardian Reviews" },
    ],
  },
  {
    heading: "Knowledge",
    items: [{ to: "/admin/kb-documents", label: "KB Documents" }],
  },
  {
    heading: "Migration",
    items: [
      { to: "/admin/migration-batches", label: "Migration Batches" },
      { to: "/admin/migration-category-statuses", label: "Category Statuses" },
      { to: "/admin/migration-id-maps", label: "ID Maps" },
    ],
  },
  {
    heading: "Reports",
    items: [{ to: "/admin/report-configs", label: "Report Configs" }],
  },
];

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return [
    "block rounded-md px-3 py-2 text-sm font-medium transition-colors",
    isActive
      ? "bg-primary-100 text-primary-800 dark:bg-primary-900 dark:text-primary-200"
      : "text-gray-700 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-gray-100",
  ].join(" ");
}

function iconNavClass({ isActive }: { isActive: boolean }): string {
  return [
    "flex items-center justify-center rounded-md p-2 transition-colors",
    isActive
      ? "bg-primary-100 text-primary-800 dark:bg-primary-900 dark:text-primary-200"
      : "text-gray-700 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-gray-100",
  ].join(" ");
}

function Sidebar() {
  const stored = localStorage.getItem("sidebarCollapsed");
  const [collapsed, setCollapsed] = useState(stored === "true");

  const storedAdmin = localStorage.getItem("sidebarAdminOpen");
  const [adminOpen, setAdminOpen] = useState(storedAdmin === "true");

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("sidebarCollapsed", String(next));
      return next;
    });
  };

  const toggleAdmin = () => {
    setAdminOpen((prev) => {
      const next = !prev;
      localStorage.setItem("sidebarAdminOpen", String(next));
      return next;
    });
  };

  const projectMatch = useMatch("/projects/:slug/*");
  const slug = projectMatch?.params.slug;

  const moduleMatch = useMatch("/projects/:slug/modules/:code/*");
  const moduleCode = moduleMatch?.params.code;

  return (
    <aside
      className={`flex shrink-0 flex-col border-r border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 transition-all duration-200 ${collapsed ? "w-14" : "w-64"}`}
    >
      {/* Header — logo + toggle */}
      <div className="flex h-14 items-center border-b border-gray-200 px-3 dark:border-gray-700">
        {!collapsed && (
          <span className="flex-1 text-lg font-semibold text-primary-700 dark:text-primary-400">
            NEX Studio
          </span>
        )}
        <button
          onClick={toggle}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto p-2" aria-label="Primary navigation">
        {/* Top-level nav */}
        <div className="space-y-1">
          {PRIMARY_NAV.map((item) =>
            collapsed ? (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={iconNavClass}
                title={item.label}
              >
                {item.icon}
              </NavLink>
            ) : (
              <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClass}>
                <span className="flex items-center gap-2">
                  {item.icon}
                  {item.label}
                </span>
              </NavLink>
            ),
          )}
        </div>

        {/* Project-context nav */}
        {slug && (
          <div className="mt-4 border-t border-gray-200 pt-3 dark:border-gray-700">
            {!collapsed && (
              <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                Project
              </p>
            )}
            <div className="space-y-1">
              {collapsed ? (
                <>
                  <NavLink
                    to={`/projects/${slug}/versions`}
                    className={iconNavClass}
                    title="Versions"
                  >
                    <Tag className="h-4 w-4" aria-hidden="true" />
                  </NavLink>
                  <NavLink
                    to={`/projects/${slug}/architect`}
                    end
                    className={iconNavClass}
                    title="Architect"
                  >
                    <Brain className="h-4 w-4" aria-hidden="true" />
                  </NavLink>
                </>
              ) : (
                <>
                  <NavLink to={`/projects/${slug}/versions`} className={navLinkClass}>
                    <span className="flex items-center gap-2">
                      <Tag className="h-4 w-4" aria-hidden="true" />
                      Versions
                    </span>
                  </NavLink>
                  <NavLink to={`/projects/${slug}/architect`} end className={navLinkClass}>
                    <span className="flex items-center gap-2">
                      <Brain className="h-4 w-4" aria-hidden="true" />
                      Architect
                    </span>
                  </NavLink>
                  {moduleCode && (
                    <NavLink
                      to={`/projects/${slug}/modules/${moduleCode}/architect`}
                      className={navLinkClass}
                    >
                      <span className="flex items-center gap-2">
                        <Brain className="h-4 w-4" aria-hidden="true" />
                        Architect ({moduleCode})
                      </span>
                    </NavLink>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        {/* Admin section — hidden when sidebar is collapsed */}
        {!collapsed && (
          <div className="mt-4 border-t border-gray-200 pt-3 dark:border-gray-700">
            <button
              type="button"
              onClick={toggleAdmin}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-gray-100 transition-colors"
              aria-expanded={adminOpen}
            >
              <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="flex-1 text-left">Admin</span>
              {adminOpen ? (
                <ChevronDown className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />
              )}
            </button>

            {adminOpen && (
              <div className="mt-2 space-y-4">
                {ADMIN_NAV.map((group) => (
                  <section key={group.heading} aria-label={group.heading}>
                    <h2 className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {group.heading}
                    </h2>
                    <div className="space-y-1">
                      {group.items.map((item) => (
                        <NavLink key={item.to} to={item.to} className={navLinkClass}>
                          {item.label}
                        </NavLink>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </div>
        )}
      </nav>

      <div className="border-t border-gray-200 p-2 dark:border-gray-700">
        {collapsed ? (
          <NavLink to="/settings" className={iconNavClass} title="Settings">
            <Settings className="h-4 w-4" aria-hidden="true" />
          </NavLink>
        ) : (
          <div className="space-y-2">
            <NavLink to="/settings" className={navLinkClass}>
              <span className="flex items-center gap-2">
                <Settings className="h-4 w-4" aria-hidden="true" />
                Settings
              </span>
            </NavLink>
            <SidebarFooter />
            <div className="flex items-center gap-2 px-3 text-xs text-gray-500 dark:text-gray-400">
              <span className="inline-block h-2 w-2 rounded-full bg-status-done" aria-hidden="true" />
              <span>Connected</span>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

export default Sidebar;
