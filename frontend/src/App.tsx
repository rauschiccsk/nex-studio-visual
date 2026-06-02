import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "./components/layout/AppLayout";
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
import MMOverviewPage from "./pages/MMOverviewPage";
import MMModulePage from "./pages/MMModulePage";
import MMDepMapPage from "./pages/MMDepMapPage";
import KnowledgeBasePage from "./pages/KnowledgeBasePage";
import ProjectSpecsPage from "./pages/ProjectSpecsPage";
import AgentTerminalPage from "./pages/AgentTerminalPage";
import DialoguePage from "./pages/DialoguePage";
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
            <Route path="projects/:slug/mm" element={<MMOverviewPage />} />
            <Route path="projects/:slug/mm/depmap" element={<MMDepMapPage />} />
            <Route path="projects/:slug/mm/:moduleId" element={<MMModulePage />} />
            <Route path="kb" element={<KnowledgeBasePage />} />
            <Route path="project-specs" element={<ProjectSpecsPage />} />
            <Route path="designer" element={<AgentTerminalPage role="designer" />} />
            <Route path="implementer" element={<AgentTerminalPage role="implementer" />} />
            <Route path="auditor" element={<AgentTerminalPage role="auditor" />} />
            <Route path="dialogue" element={<DialoguePage />} />
            <Route path="credentials" element={<CredentialsPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
