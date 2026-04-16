import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "./components/layout/AppLayout";
import ProtectedRoute from "./components/ProtectedRoute";

import ArchitectPage from "./pages/ArchitectPage";
import DashboardPage from "./pages/DashboardPage";
import DelegationPage from "./pages/DelegationPage";
import KnowledgeBasePage from "./pages/KnowledgeBasePage";
import LoginPage from "./pages/LoginPage";
import MigrationPage from "./pages/MigrationPage";
import ModuleRegistryPage from "./pages/ModuleRegistryPage";
import NewProjectPage from "./pages/NewProjectPage";
import NotFoundPage from "./pages/NotFoundPage";
import ProjectPage from "./pages/ProjectPage";
import ProjectsPage from "./pages/ProjectsPage";
import ReportsPage from "./pages/ReportsPage";
import SettingsPage from "./pages/SettingsPage";
import SpecificationPage from "./pages/SpecificationPage";
import TasksPage from "./pages/TasksPage";

/**
 * Root application component — declares the full route table for NEX Studio.
 *
 * The routing tree mirrors DESIGN.md § 3.1 exactly: a public /login route, a
 * protected AppLayout parent that wraps all authenticated pages, and a
 * catch-all 404. ArchitectPage is reused at both the project and module
 * scopes, so it appears twice with different URL patterns.
 */
function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public route */}
        <Route path="/login" element={<LoginPage />} />

        {/* Protected routes — share the AppLayout chrome */}
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
          <Route path="projects/:slug" element={<ProjectPage />} />
          <Route path="projects/:slug/spec" element={<SpecificationPage />} />
          <Route
            path="projects/:slug/modules"
            element={<ModuleRegistryPage />}
          />
          <Route path="projects/:slug/architect" element={<ArchitectPage />} />
          <Route
            path="projects/:slug/modules/:code/architect"
            element={<ArchitectPage />}
          />
          <Route path="projects/:slug/tasks" element={<TasksPage />} />
          <Route path="projects/:slug/delegate" element={<DelegationPage />} />
          <Route path="projects/:slug/reports" element={<ReportsPage />} />
          <Route path="projects/:slug/migration" element={<MigrationPage />} />
          <Route path="projects/:slug/kb" element={<KnowledgeBasePage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        {/* Redirect legacy / unknown top-level routes */}
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
