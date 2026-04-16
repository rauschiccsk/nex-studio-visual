import { Outlet } from "react-router-dom";
import Header from "./Header";
import Sidebar from "./Sidebar";

/**
 * Primary application layout: sidebar + header + main content area.
 *
 * Rendered as a parent route for every authenticated page (DESIGN.md § 3.2).
 * Child routes are rendered via <Outlet />.
 */
function AppLayout() {
  return (
    <div className="flex h-full w-full bg-gray-50">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default AppLayout;
