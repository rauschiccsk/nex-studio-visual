import { useLocation } from "react-router-dom";

const breadcrumbMap: Record<string, string> = {
  "/": "Dashboard",
  "/projects": "Projects",
  "/kb": "Knowledge Base",
  "/settings": "Settings",
};

export default function Topbar() {
  const location = useLocation();

  const label = breadcrumbMap[location.pathname] ?? "NEX Studio";

  return (
    <header className="h-10 flex-shrink-0 bg-slate-900 border-b border-slate-800 flex items-center px-3 gap-3 z-10 select-none">
      {/* Connected indicator */}
      <div className="flex items-center gap-1.5 shrink-0">
        <div className="w-2 h-2 rounded-full bg-green-400" />
        <span className="text-xs text-slate-300 font-medium">Connected</span>
      </div>

      {/* Breadcrumb */}
      <div className="flex items-center gap-1.5 flex-1 min-w-0 overflow-hidden text-xs text-slate-500">
        <span className="text-slate-600">/</span>
        <span className="text-slate-300">{label}</span>
      </div>
    </header>
  );
}
