import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "./components/layout/AppLayout";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import { ThemeProvider } from "./contexts/ThemeContext";
import { useAuthStore } from "./store/authStore";
import { useSessionKeepAlive } from "./hooks/useSessionKeepAlive";

import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import GettingStartedPage from "./pages/GettingStartedPage";
import ProjectsPage from "./pages/ProjectsPage";
import NewProjectPage from "./pages/NewProjectPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import NewVersionPage from "./pages/NewVersionPage";
import VersionDetailPage from "./pages/VersionDetailPage";
import BacklogPage from "./pages/BacklogPage";
import MetricsPage from "./pages/MetricsPage";
import KnowledgeBasePage from "./pages/KnowledgeBasePage";
import UpdatesPage from "./pages/UpdatesPage";
import RiadiaceCentrumPage from "./pages/RiadiaceCentrumPage";
import SpecifikaciaPage from "./pages/SpecifikaciaPage";
import CredentialsPage from "./pages/CredentialsPage";
import CustomersPage from "./pages/CustomersPage";
import UatPage from "./pages/UatPage";
import ProdPage from "./pages/ProdPage";
import SettingsPage from "./pages/SettingsPage";

function App() {
  const username = useAuthStore((s) => s.user?.username);

  // Silently renew the session while the user is actively working so a
  // short-lived JWT never bounces them to /login mid-work (idle sessions still
  // expire — see the hook's security posture).
  useSessionKeepAlive();

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
            <Route path="getting-started" element={<GettingStartedPage />} />
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
            {/* v2 spine STEP 1 (Chrbtica): the Riadiace centrum is ONE conversation-centred
                screen that replaces the old Vývoj build board + the AI Agent tab. The retired
                /vyvoj + /ai-agent routes redirect here so stale bookmarks
                survive; /cockpit + /coordinator keep their existing hop (→ /vyvoj / → /ai-agent,
                which redirect onward to /riadiace-centrum). Špecifikácia is the read-only spec
                shell (real .md wired in a later step). */}
            <Route path="riadiace-centrum" element={<RiadiaceCentrumPage />} />
            <Route path="specifikacia" element={<SpecifikaciaPage />} />
            <Route path="ai-agent" element={<Navigate to="/riadiace-centrum" replace />} />
            <Route path="coordinator" element={<Navigate to="/ai-agent" replace />} />
            <Route path="vyvoj" element={<Navigate to="/riadiace-centrum" replace />} />
            <Route path="cockpit" element={<Navigate to="/vyvoj" replace />} />
            {/* v2: per-customer deploy nav surfaces (design §3 / §4.1).
                Zákazníci = CR-V2-025 (project-scoped registry).
                UAT / PROD = CR-V2-027 (version × customer matrix + Nasadiť + Akceptovať gate). */}
            <Route path="zakaznici" element={<CustomersPage />} />
            <Route path="uat" element={<UatPage />} />
            <Route path="prod" element={<ProdPage />} />
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
