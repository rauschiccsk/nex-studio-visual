import { NavLink } from "react-router-dom";

/**
 * Application sidebar — provides project list placeholder and primary navigation links.
 *
 * Per DESIGN.md § 3.2, the Sidebar is responsible for:
 *   - Project list (to be wired to projectStore in a later task)
 *   - Navigation links (Dashboard, Projects, Settings)
 *   - Connection status indicator
 */
function Sidebar() {
  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    [
      "block rounded-md px-3 py-2 text-sm font-medium transition-colors",
      isActive
        ? "bg-primary-100 text-primary-800"
        : "text-gray-700 hover:bg-gray-100 hover:text-gray-900",
    ].join(" ");

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-gray-200 bg-white">
      <div className="flex h-14 items-center border-b border-gray-200 px-4">
        <span className="text-lg font-semibold text-primary-700">
          NEX Studio
        </span>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        <NavLink to="/" end className={navLinkClass}>
          Dashboard
        </NavLink>
        <NavLink to="/projects" className={navLinkClass}>
          Projects
        </NavLink>
        <NavLink to="/settings" className={navLinkClass}>
          Settings
        </NavLink>
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
