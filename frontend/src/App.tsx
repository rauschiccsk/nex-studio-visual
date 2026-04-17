import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "./components/layout/AppLayout";
import ProtectedRoute from "./components/auth/ProtectedRoute";

import ArchitectMessagePage from "./pages/ArchitectMessagePage";
import ArchitectPage from "./pages/ArchitectPage";
import ArchitectSessionPage from "./pages/ArchitectSessionPage";
import AutoFixAttemptPage from "./pages/AutoFixAttemptPage";
import BugFixTaskPage from "./pages/BugFixTaskPage";
import BugPage from "./pages/BugPage";
import DashboardPage from "./pages/DashboardPage";
import DelegationAdminPage from "./pages/DelegationAdminPage";
import DelegationPage from "./pages/DelegationPage";
import DesignDocumentPage from "./pages/DesignDocumentPage";
import EpicPage from "./pages/EpicPage";
import ExecutionLogPage from "./pages/ExecutionLogPage";
import FeatPage from "./pages/FeatPage";
import GuardianPrecedentPage from "./pages/GuardianPrecedentPage";
import GuardianReviewPage from "./pages/GuardianReviewPage";
import KbDocumentPage from "./pages/KbDocumentPage";
import KnowledgeBasePage from "./pages/KnowledgeBasePage";
import LoginPage from "./pages/LoginPage";
import MigrationBatchPage from "./pages/MigrationBatchPage";
import MigrationCategoryStatusPage from "./pages/MigrationCategoryStatusPage";
import MigrationIdMapPage from "./pages/MigrationIdMapPage";
import MigrationPage from "./pages/MigrationPage";
import ModuleDependencyPage from "./pages/ModuleDependencyPage";
import ModuleRegistryPage from "./pages/ModuleRegistryPage";
import NewProjectPage from "./pages/NewProjectPage";
import NotFoundPage from "./pages/NotFoundPage";
import ProjectAdminPage from "./pages/ProjectAdminPage";
import ProjectMemberPage from "./pages/ProjectMemberPage";
import ProjectModulePage from "./pages/ProjectModulePage";
import ProfessionalSpecificationPage from "./pages/ProfessionalSpecificationPage";
import ProjectPage from "./pages/ProjectPage";
import ProjectsPage from "./pages/ProjectsPage";
import RawSpecificationPage from "./pages/RawSpecificationPage";
import ReportConfigPage from "./pages/ReportConfigPage";
import ReportsPage from "./pages/ReportsPage";
import SettingsPage from "./pages/SettingsPage";
import SpecificationPage from "./pages/SpecificationPage";
import TaskAdminPage from "./pages/TaskAdminPage";
import TasksPage from "./pages/TasksPage";
import UserPage from "./pages/UserPage";
import UserSessionPage from "./pages/UserSessionPage";
import VersionDetailPage from "./pages/VersionDetailPage";
import VersionsPage from "./pages/VersionsPage";

/**
 * Root application component — declares the full route table for NEX Studio.
 *
 * The end-user routing tree mirrors DESIGN.md § 3.1: a public /login route, a
 * protected AppLayout parent that wraps all authenticated pages, and a
 * catch-all 404. ArchitectPage is reused at both the project and module
 * scopes, so it appears twice with different URL patterns.
 *
 * The ``/admin/*`` subtree (``/admin/users``, ``/admin/projects``,
 * ``/admin/guardian-precedents``, ``/admin/migration-batches``,
 * ``/admin/migration-category-statuses``, ``/admin/migration-id-maps``,
 * ``/admin/project-members``, ``/admin/project-modules``,
 * ``/admin/architect-sessions``, ``/admin/architect-messages``,
 * ``/admin/design-documents``, ``/admin/epics``, ``/admin/feats``,
 * ``/admin/tasks``, ``/admin/auto-fix-attempts``,
 * ``/admin/kb-documents``, ``/admin/module-dependencies``,
 * ``/admin/raw-specifications``, ``/admin/professional-specifications``,
 * ``/admin/report-configs``, ``/admin/delegations``,
 * ``/admin/execution-logs``, ``/admin/guardian-reviews``,
 * ``/admin/user-sessions``)
 * is NOT enumerated in
 * DESIGN.md § 3.1 — those routes are Feat 6 admin-CRUD additions that
 * sit alongside the end-user navigation documented in § 3.1. Treat
 * this block as an intentional extension of § 3.1, not drift: new
 * entity CRUD surfaces land here until DESIGN.md § 3.1 is amended to
 * enumerate them.
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
          <Route path="projects/:slug/versions" element={<VersionsPage />} />
          <Route
            path="projects/:slug/versions/:vid"
            element={<VersionDetailPage />}
          />
          <Route path="projects/:slug/tasks" element={<TasksPage />} />
          <Route path="projects/:slug/delegate" element={<DelegationPage />} />
          <Route path="projects/:slug/reports" element={<ReportsPage />} />
          <Route path="projects/:slug/migration" element={<MigrationPage />} />
          <Route path="projects/:slug/kb" element={<KnowledgeBasePage />} />
          <Route path="settings" element={<SettingsPage />} />

          {/* Admin CRUD pages (Feat 6) — one route per entity. */}
          <Route
            path="admin/guardian-precedents"
            element={<GuardianPrecedentPage />}
          />
          <Route path="admin/users" element={<UserPage />} />
          <Route path="admin/projects" element={<ProjectAdminPage />} />
          <Route path="admin/bugs" element={<BugPage />} />
          <Route path="admin/bug-fix-tasks" element={<BugFixTaskPage />} />
          <Route
            path="admin/migration-batches"
            element={<MigrationBatchPage />}
          />
          <Route
            path="admin/migration-category-statuses"
            element={<MigrationCategoryStatusPage />}
          />
          <Route
            path="admin/migration-id-maps"
            element={<MigrationIdMapPage />}
          />
          <Route
            path="admin/project-members"
            element={<ProjectMemberPage />}
          />
          <Route
            path="admin/project-modules"
            element={<ProjectModulePage />}
          />
          <Route
            path="admin/architect-sessions"
            element={<ArchitectSessionPage />}
          />
          <Route
            path="admin/architect-messages"
            element={<ArchitectMessagePage />}
          />
          <Route
            path="admin/design-documents"
            element={<DesignDocumentPage />}
          />
          <Route path="admin/epics" element={<EpicPage />} />
          <Route path="admin/feats" element={<FeatPage />} />
          <Route path="admin/tasks" element={<TaskAdminPage />} />
          <Route
            path="admin/auto-fix-attempts"
            element={<AutoFixAttemptPage />}
          />
          <Route path="admin/kb-documents" element={<KbDocumentPage />} />
          <Route
            path="admin/module-dependencies"
            element={<ModuleDependencyPage />}
          />
          <Route
            path="admin/raw-specifications"
            element={<RawSpecificationPage />}
          />
          <Route
            path="admin/professional-specifications"
            element={<ProfessionalSpecificationPage />}
          />
          <Route
            path="admin/report-configs"
            element={<ReportConfigPage />}
          />
          <Route
            path="admin/delegations"
            element={<DelegationAdminPage />}
          />
          <Route
            path="admin/execution-logs"
            element={<ExecutionLogPage />}
          />
          <Route
            path="admin/guardian-reviews"
            element={<GuardianReviewPage />}
          />
          <Route
            path="admin/user-sessions"
            element={<UserSessionPage />}
          />
        </Route>

        {/* Redirect legacy / unknown top-level routes */}
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
