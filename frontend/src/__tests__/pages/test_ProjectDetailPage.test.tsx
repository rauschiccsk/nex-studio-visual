/**
 * ProjectDetailPage — Fast-Fix Lane entry (F-009 §4 CR-B, CR-NS-095).
 *
 * The "Rýchla oprava" button opens a modal; submitting the directive POSTs to
 * /pipeline/fast-fix, pins the auto-created PATCH version into the active context
 * (project FIRST, then version), and navigates to the Vývoj board (/vyvoj,
 * renamed from /cockpit in CR-V2-019).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const {
  navigateMock,
  listProjectsApiMock,
  getProjectApiMock,
  deleteProjectApiMock,
  listVersionsMock,
  getVersionMock,
  startFastFixApiMock,
  setSelectedProjectMock,
  setSelectedVersionMock,
  authStateMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  listProjectsApiMock: vi.fn(),
  getProjectApiMock: vi.fn(),
  deleteProjectApiMock: vi.fn(),
  listVersionsMock: vi.fn(),
  getVersionMock: vi.fn(),
  startFastFixApiMock: vi.fn(),
  setSelectedProjectMock: vi.fn(),
  setSelectedVersionMock: vi.fn(),
  authStateMock: { user: { role: "ri" } as { role: string } | null },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useParams: () => ({ slug: "demo" }),
    useLocation: () => ({ pathname: "/projects/demo", search: "", hash: "", key: "t", state: null }),
  };
});

vi.mock("@/services/api/projects", () => ({
  listProjectsApi: listProjectsApiMock,
  getProjectApi: getProjectApiMock,
  deleteProjectApi: deleteProjectApiMock,
}));
vi.mock("@/services/api/versions", () => ({ listVersions: listVersionsMock, getVersion: getVersionMock }));
vi.mock("@/services/api/pipeline", () => ({ startFastFixApi: startFastFixApiMock }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: setSelectedProjectMock, setSelectedVersion: setSelectedVersionMock }),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) => selector(authStateMock),
}));

// ── Fixtures ───────────────────────────────────────────────────────────────────

const project: ProjectRead = {
  id: "p1",
  name: "Demo",
  slug: "demo",
  type: "standard",
  auth_mode: "password",
  description: "",
  status: "active",
  backend_port: null,
  frontend_port: null,
  db_port: null,
  repo_url: null,
  source_path: null,
  kb_path: null,
  guardian_enabled: false,
  custom_development_enabled: false,
  created_by: "u1",
  owner_id: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  has_prod_deploy: false,
};

const baseVersion: Version = {
  id: "v1",
  project_id: "p1",
  version_number: "v0.6.0",
  name: "Initial",
  status: "released",
  description: null,
  target_date: null,
  release_date: "2026-06-01",
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  epic_count: 0,
  epics_done: 0,
  bug_count: 0,
};

const patchVersion: Version = { ...baseVersion, id: "v2", version_number: "v0.6.1", name: "Rýchla oprava", status: "planned" };

beforeEach(() => {
  vi.clearAllMocks();
  listProjectsApiMock.mockResolvedValue({ items: [project], total: 1, skip: 0, limit: 100 });
  getProjectApiMock.mockResolvedValue(project);
  deleteProjectApiMock.mockResolvedValue(undefined);
  listVersionsMock.mockResolvedValue([baseVersion]);
  getVersionMock.mockResolvedValue(patchVersion);
  startFastFixApiMock.mockResolvedValue({ version_id: "v2", board: { state: null, recent_messages: [] } });
  authStateMock.user = { role: "ri" }; // admin by default; individual tests may override
});

async function importPage() {
  return (await import("@/pages/ProjectDetailPage")).default;
}

describe("ProjectDetailPage — Fast-Fix Lane (CR-NS-095)", () => {
  it("posts the directive, pins the patch version (project first), and opens the cockpit", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    // Entry button appears once the project + its (>0) versions load.
    const entry = await screen.findByRole("button", { name: /^rýchla oprava$/i });
    await userEvent.click(entry);

    await userEvent.type(screen.getByLabelText(/popis opravy/i), "Oprav preklep v sidebare.");
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    await waitFor(() =>
      expect(startFastFixApiMock).toHaveBeenCalledWith("p1", "Oprav preklep v sidebare."),
    );
    // CR-V2-019 (OQ-7): the build board route is /vyvoj (renamed from /cockpit).
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/vyvoj"));

    expect(getVersionMock).toHaveBeenCalledWith("v2");
    expect(setSelectedProjectMock).toHaveBeenCalledWith({ slug: "demo", name: "Demo" });
    expect(setSelectedVersionMock).toHaveBeenCalledWith({ versionId: "v2", versionNumber: "v0.6.1" });

    // Context must be pinned project-FIRST (setSelectedProject clears the version slot).
    const projOrder = setSelectedProjectMock.mock.invocationCallOrder[0] ?? 0;
    const verOrder = setSelectedVersionMock.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER;
    expect(projOrder).toBeLessThan(verOrder);
  });

  it("submit is disabled on an empty directive and the modal can be cancelled", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^rýchla oprava$/i }));
    expect(screen.getByRole("button", { name: /spustiť rýchlu opravu/i })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /zrušiť/i }));
    expect(screen.queryByLabelText(/popis opravy/i)).not.toBeInTheDocument();
    expect(startFastFixApiMock).not.toHaveBeenCalled();
  });

  it("surfaces a backend error without navigating away", async () => {
    startFastFixApiMock.mockRejectedValue(new Error("Project has no semver version to patch from"));
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^rýchla oprava$/i }));
    await userEvent.type(screen.getByLabelText(/popis opravy/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /spustiť rýchlu opravu/i }));

    expect(await screen.findByText(/no semver version to patch/i)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});

describe("ProjectDetailPage — guarded delete (CR-V2-027)", () => {
  it("requires typing DELETE, then deletes (GitHub by default) and returns to the list", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^zmazať projekt$/i }));

    const confirm = screen.getByRole("button", { name: /zmazať natrvalo/i });
    expect(confirm).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/na potvrdenie napíš/i), "ZMAZAŤ");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    await waitFor(() => expect(deleteProjectApiMock).toHaveBeenCalledWith("p1", true));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects"));
  });

  it("does not delete on a wrong confirmation word", async () => {
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: /^zmazať projekt$/i }));
    await userEvent.type(screen.getByLabelText(/na potvrdenie napíš/i), "zmazať"); // wrong case

    expect(screen.getByRole("button", { name: /zmazať natrvalo/i })).toBeDisabled();
    expect(deleteProjectApiMock).not.toHaveBeenCalled();
  });

  it("disables delete for a non-admin (role shu)", async () => {
    authStateMock.user = { role: "shu" };
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /^zmazať projekt$/i })).toBeDisabled();
  });

  it("disables delete once the project has a PROD deploy", async () => {
    getProjectApiMock.mockResolvedValue({ ...project, has_prod_deploy: true });
    const ProjectDetailPage = await importPage();
    render(<ProjectDetailPage />);

    expect(await screen.findByRole("button", { name: /^zmazať projekt$/i })).toBeDisabled();
  });
});
