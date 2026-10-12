import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { AppShell } from "nex-shared";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import { PersistentTerminalsLayer } from "@/components/PersistentTerminalsLayer";
import { useAuthStore } from "@/store/authStore";
import { useUserLayoutStore } from "@/store/userLayoutStore";

export default function AppLayout() {
  // DEV-82: the person's own layout (panel widths, collapsed sidebar) — read once per signed-in person.
  const userId = useAuthStore((s) => s.user?.id);
  const loadLayout = useUserLayoutStore((s) => s.load);
  useEffect(() => {
    if (userId) void loadLayout();
  }, [userId, loadLayout]);

  return (
    <AppShell sidebar={<Sidebar />} header={<Topbar />}>
      <Outlet />
      {/* Overlay anchored in AppShell's relative <main> region. */}
      <PersistentTerminalsLayer />
    </AppShell>
  );
}
