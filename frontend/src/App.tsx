import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "./components/layout/AppLayout";
import ComingSoonPage from "./pages/ComingSoonPage";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import { ThemeProvider } from "./contexts/ThemeContext";
import { useAuthStore } from "./store/authStore";

import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import ProjectsPage from "./pages/ProjectsPage";
import NewProjectPage from "./pages/NewProjectPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import NewVersionPage from "./pages/NewVersionPage";
import VersionDetailPage from "./pages/VersionDetailPage";
import BacklogPage from "./pages/BacklogPage";
import MetricsPage from "./pages/MetricsPage";
import KnowledgeBasePage from "./pages/KnowledgeBasePage";
import UpdatesPage from "./pages/UpdatesPage";
import AgentTerminalPage from "./pages/AgentTerminalPage";
import CockpitPage from "./pages/CockpitPage";
import CredentialsPage from "./pages/CredentialsPage";
import SettingsPage from "./pages/SettingsPage";

function App() {
  const username = useAuthStore((s) => s.user?.username);

  return (
    <ThemeProvider username={username}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route
            element={
              <ProtectedRoute>
                <AppLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/new" element={<NewProjectPage />} />
            <Route path="projects/:slug" element={<ProjectDetailPage />} />
            <Route path="projects/:slug/versions/new" element={<NewVersionPage />} />
            <Route path="projects/:slug/versions/:versionId" element={<VersionDetailPage />} />
            <Route path="projects/:slug/backlog" element={<BacklogPage />} />
            <Route path="projects/:slug/metrics" element={<MetricsPage />} />
            <Route path="kb" element={<KnowledgeBasePage />} />
            {/* v2 (CR-V2-020): /project-specs (📖 Špecifikácie) removed — the
                page is retired. The single design doc now lives in Vývoj →
                Návrh (CR-V2-021); /kb remains for ICC-wide knowledge. The
                wildcard route below redirects any stale /project-specs link
                to the dashboard. */}
            {/* v2 (CR-V2-019, OQ-7): the AI Agent live terminal route renamed
                /coordinator → /ai-agent (matches the new vocabulary). CR-V2-022
                landed the event-rendered AI Agent chrome (transcript + 4-phase
                strip + relay + Helpers panel) and re-keyed the AgentRole store
                slot "coordinator" → "ai-agent" (the backend wire/charter slug,
                so sessions are not broken). The old /coordinator path redirects
                so live links/bookmarks survive. CR-NS-065: the standalone
                /dialogue page was retired. */}
            <Route path="ai-agent" element={<AgentTerminalPage role="ai-agent" />} />
            <Route path="coordinator" element={<Navigate to="/ai-agent" replace />} />
            {/* v2 (CR-V2-019, OQ-7): the build board route renamed
                /cockpit → /vyvoj (Orchestrácia → Vývoj). The horizontal
                4-phase Vývoj board lands in CR-V2-021; CockpitPage is the
                interim shell. Old /cockpit path redirects. */}
            <Route path="vyvoj" element={<CockpitPage />} />
            <Route path="cockpit" element={<Navigate to="/vyvoj" replace />} />
            {/* v2 (CR-V2-019): per-customer deploy nav surfaces (design §3 / §4.1).
                Routes resolve to a lightweight "pripravuje sa" placeholder so the
                new nav items never 404; their real pages land in Milestone G —
                Zákazníci = CR-V2-025, UAT / PROD = CR-V2-027. */}
            <Route
              path="zakaznici"
              element={
                <ComingSoonPage
                  title="Zákazníci"
                  description="Register zákazníkov projektu — pridávanie cez formulár, integrácie a per-zákazník nasadenie. Pripravuje sa."
                />
              }
            />
            <Route
              path="uat"
              element={
                <ComingSoonPage
                  title="UAT"
                  description="Per-zákazník UAT nasadenie a akceptácia (verzia × zákazník). Pripravuje sa."
                />
              }
            />
            <Route
              path="prod"
              element={
                <ComingSoonPage
                  title="PROD"
                  description="Per-zákazník produkčné nasadenie (verzia × zákazník). Pripravuje sa."
                />
              }
            />
            <Route path="credentials" element={<CredentialsPage />} />
            <Route path="updates" element={<UpdatesPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
