import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import { PersistentTerminalsLayer } from "@/components/PersistentTerminalsLayer";

export default function AppLayout() {
  return (
    <div className="flex h-full w-full bg-slate-950">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Topbar />
        <main className="relative flex-1 overflow-y-auto">
          <Outlet />
          <PersistentTerminalsLayer />
        </main>
      </div>
    </div>
  );
}
