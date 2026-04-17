import { NavLink, useMatch } from "react-router-dom";
import { Tag } from "lucide-react";

/**
 * Application sidebar — primary navigation surface for NEX Studio.
 *
 * Per DESIGN.md § 3.2 the Sidebar is responsible for:
 *   - Top-level end-user navigation (Dashboard, Projects, Settings)
 *   - Project-context navigation (Versions, etc.) when viewing a project
 *   - Connection status indicator
 *
 * Feat 6 introduces a parallel set of admin-CRUD pages that sit alongside
 * the end-user routes described in DESIGN.md § 3.1 (see App.tsx comment for
 * the extension rationale).  Each admin page has its own ``/admin/<slug>``
 * route, so the Sidebar exposes a grouped link list for them.  Groups are
 * purely visual — they mirror the domain clustering used throughout the
 * backend (users, projects, specs, architect, tasks, bugs, delegation,
 * guardian, knowledge, migration, reports) so an operator can jump
 * directly from one entity surface to a related one without going back
 * to the dashboard.
 */

type NavItem = {
  to: string;
  label: string;
  /** When true, the NavLink is matched with ``end`` so it only activates
   *  on the exact path (required for the root ``/`` dashboard link). */
  end?: boolean;
};

type NavGroup = {
  heading: string;
  items: NavItem[];
};

/**
 * Top-level end-user navigation.  Mirrors DESIGN.md § 3.1 routes that do
 * not require a project slug.
 */
const PRIMARY_NAV: NavItem[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/projects", label: "Projects" },
  { to: "/settings", label: "Settings" },
];

/**
 * Admin-CRUD navigation groups — one group per backend domain.  The order
 * of items within each group reflects a natural "root entity first,
 * child entities after" reading order so that newcomers can trace the
 * dependency direction by skimming the list.
 */
const ADMIN_NAV: NavGroup[] = [
  {
    heading: "Access",
    items: [
      { to: "/admin/users", label: "Users" },
      { to: "/admin/user-sessions", label: "User Sessions" },
      { to: "/admin/project-members", label: "Project Members" },
    ],
  },
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
      {
        to: "/admin/professional-specifications",
        label: "Professional Specifications",
      },
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
      {
        to: "/admin/migration-category-statuses",
        label: "Category Statuses",
      },
      { to: "/admin/migration-id-maps", label: "ID Maps" },
    ],
  },
  {
    heading: "Reports",
    items: [{ to: "/admin/report-configs", label: "Report Configs" }],
  },
];

/**
 * Tailwind class string for a NavLink based on its ``isActive`` state.
 * Extracted to a standalone function so the two NavLink usages below
 * (primary + admin) share a single source of truth for styling.
 */
function navLinkClass({ isActive }: { isActive: boolean }): string {
  return [
    "block rounded-md px-3 py-2 text-sm font-medium transition-colors",
    isActive
      ? "bg-primary-100 text-primary-800"
      : "text-gray-700 hover:bg-gray-100 hover:text-gray-900",
  ].join(" ");
}

function Sidebar() {
  /** Detect whether the current URL is inside ``/projects/:slug/*``. */
  const projectMatch = useMatch("/projects/:slug/*");
  const slug = projectMatch?.params.slug;

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-gray-200 bg-white">
      <div className="flex h-14 items-center border-b border-gray-200 px-4">
        <span className="text-lg font-semibold text-primary-700">
          NEX Studio
        </span>
      </div>

      <nav
        className="flex-1 overflow-y-auto p-3"
        aria-label="Primary navigation"
      >
        {/* Top-level end-user links (DESIGN.md § 3.1). */}
        <div className="space-y-1">
          {PRIMARY_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={navLinkClass}
            >
              {item.label}
            </NavLink>
          ))}
        </div>

        {/* Project-context navigation — visible only when viewing a
            specific project (``/projects/:slug/…``). */}
        {slug && (
          <div className="mt-4 border-t border-gray-200 pt-3">
            <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
              Project
            </p>
            <div className="space-y-1">
              <NavLink
                to={`/projects/${slug}/versions`}
                className={navLinkClass}
              >
                <span className="flex items-center gap-2">
                  <Tag className="h-4 w-4" aria-hidden="true" />
                  Versions
                </span>
              </NavLink>
            </div>
          </div>
        )}

        {/* Admin-CRUD surface (Feat 6).  Each heading is purely visual
            and is rendered as an ``h2`` so screen readers can navigate
            the groups as document sections. */}
        <div className="mt-6 border-t border-gray-200 pt-4">
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
            Admin
          </p>
          <div className="space-y-4">
            {ADMIN_NAV.map((group) => (
              <section key={group.heading} aria-label={group.heading}>
                <h2 className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  {group.heading}
                </h2>
                <div className="space-y-1">
                  {group.items.map((item) => (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={navLinkClass}
                    >
                      {item.label}
                    </NavLink>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      </nav>

      <div className="border-t border-gray-200 p-3">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span
            className="inline-block h-2 w-2 rounded-full bg-status-done"
            aria-hidden="true"
          />
          <span>Connected</span>
        </div>
      </div>
    </aside>
  );
}

export default Sidebar;
