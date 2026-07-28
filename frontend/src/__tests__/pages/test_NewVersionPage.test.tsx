/**
 * NewVersionPage — a failure on the founding screen must be SEEN.
 *
 * The dirty-tree guard ("Uložiť ich" / "Zahodiť") and the nex-shared upgrade all ran through empty
 * catch blocks: a rejected commit flipped the button back and left the same warning card standing,
 * with nothing said, and an upgrade whose pin could not be committed dismissed the prompt and
 * reported the library up to date while the tree it had just dirtied went unnoticed. These tests
 * pin the honest behaviour — every failure renders, and the guard re-checks the tree for real.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import { ApiError } from "@/services/api";
import type { GitStatus, NexsharedStatus } from "@/services/api/projects";
import type { ProjectRead } from "@/types";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const {
  navigateMock,
  listProjectsApiMock,
  getNexsharedStatusApiMock,
  upgradeNexsharedApiMock,
  getGitStatusApiMock,
  commitGitApiMock,
  discardGitApiMock,
  listVersionsMock,
  createVersionMock,
  writeZadanieMock,
  postPipelineActionApiMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  listProjectsApiMock: vi.fn(),
  getNexsharedStatusApiMock: vi.fn(),
  upgradeNexsharedApiMock: vi.fn(),
  getGitStatusApiMock: vi.fn(),
  commitGitApiMock: vi.fn(),
  discardGitApiMock: vi.fn(),
  listVersionsMock: vi.fn(),
  createVersionMock: vi.fn(),
  writeZadanieMock: vi.fn(),
  postPipelineActionApiMock: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock, useParams: () => ({ slug: "demo" }) };
});

vi.mock("@/services/api/projects", () => ({
  listProjectsApi: listProjectsApiMock,
  getNexsharedStatusApi: getNexsharedStatusApiMock,
  upgradeNexsharedApi: upgradeNexsharedApiMock,
  getGitStatusApi: getGitStatusApiMock,
  commitGitApi: commitGitApiMock,
  discardGitApi: discardGitApiMock,
}));
vi.mock("@/services/api/versions", () => ({
  listVersions: listVersionsMock,
  createVersion: createVersionMock,
  writeZadanie: writeZadanieMock,
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: postPipelineActionApiMock }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ setSelectedProject: vi.fn(), setSelectedVersion: vi.fn() }),
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
  source_path: "/opt/projects/demo",
  kb_path: null,
  guardian_enabled: false,
  setup_warnings: [],
  custom_development_enabled: false,
  created_by: "u1",
  owner_id: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  has_prod_deploy: false,
};

const dirtyTree: GitStatus = {
  clean: false,
  dirty_count: 2,
  files: [
    { code: " M", path: "backend/api/routes/projects.py" },
    { code: "??", path: "docs/notes.md" },
  ],
  truncated: false,
};
const cleanTree: GitStatus = { clean: true, dirty_count: 0, files: [], truncated: false };

const behindNexshared: NexsharedStatus = {
  current: "0.18.0",
  latest: "0.19.0",
  behind: 1,
  up_to_date: false,
  changelog: [{ version: "0.19.0", body: "Nové tokeny." }],
};

beforeEach(() => {
  vi.clearAllMocks();
  listProjectsApiMock.mockResolvedValue({ items: [project], total: 1, skip: 0, limit: 100 });
  listVersionsMock.mockResolvedValue([]);
  getNexsharedStatusApiMock.mockResolvedValue({ ...behindNexshared, behind: 0, up_to_date: true, changelog: [] });
  getGitStatusApiMock.mockResolvedValue(cleanTree);
  commitGitApiMock.mockResolvedValue({ ok: true });
  discardGitApiMock.mockResolvedValue({ ok: true });
});

async function renderPage() {
  const NewVersionPage = (await import("@/pages/NewVersionPage")).default;
  render(<NewVersionPage />);
}

describe("NewVersionPage — dirty-tree guard reports its failures", () => {
  it('says why "Uložiť ich" failed instead of silently re-showing the guard', async () => {
    getGitStatusApiMock.mockResolvedValue(dirtyTree);
    // What the container actually answers when the project's own pre-commit hook cannot run there.
    commitGitApiMock.mockRejectedValue(new ApiError(400, "Commit zlyhal: pre-commit hook (ruff) not found"));

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /uložiť ich/i }));

    expect(await screen.findByText(/Uloženie zmien zlyhalo/i)).toBeInTheDocument();
    // The raw backend reason stays available behind the "Technický detail" disclosure.
    expect(screen.getByText(/pre-commit hook \(ruff\) not found/i)).toBeInTheDocument();
    // The guard stays up (the tree is still dirty) and founding stays blocked.
    expect(screen.getByRole("button", { name: /uložiť ich/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeDisabled();
  });

  it('says why "Zahodiť" failed', async () => {
    getGitStatusApiMock.mockResolvedValue(dirtyTree);
    discardGitApiMock.mockRejectedValue(new ApiError(400, "Zahodenie zmien zlyhalo"));

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /^zahodiť$/i }));
    await userEvent.click(screen.getByRole("button", { name: /naozaj zahodiť/i }));

    expect(await screen.findByText(/Zahodenie zmien zlyhalo — /i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeDisabled();
  });

  it("clears the guard when the commit really succeeds", async () => {
    getGitStatusApiMock.mockResolvedValueOnce(dirtyTree).mockResolvedValue(cleanTree);

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /uložiť ich/i }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /uložiť ich/i })).not.toBeInTheDocument());
    expect(screen.queryByText(/zlyhalo/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeEnabled();
  });

  it("does not pretend the tree is clean when the preflight itself fails", async () => {
    getGitStatusApiMock.mockRejectedValue(new ApiError(500, "git unavailable"));

    await renderPage();

    expect(await screen.findByText(/Overenie neuložených zmien projektu zlyhalo/i)).toBeInTheDocument();
  });
});

describe("NewVersionPage — nex-shared upgrade tells the truth about committed", () => {
  beforeEach(() => {
    getNexsharedStatusApiMock.mockResolvedValue(behindNexshared);
  });

  it("reports a written-but-unsaved pin and re-checks the tree instead of claiming up to date", async () => {
    upgradeNexsharedApiMock.mockResolvedValue({ upgraded: true, target_version: "0.19.0", committed: false });
    // The upgrade dirties the tree: the pin was rewritten but never committed.
    getGitStatusApiMock.mockResolvedValueOnce(cleanTree).mockResolvedValue(dirtyTree);

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));

    expect(await screen.findByText(/nepodarilo sa ho uložiť/i)).toBeInTheDocument();
    // The guard re-evaluated against reality: the now-dirty tree blocks founding again.
    await waitFor(() => expect(getGitStatusApiMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: /uložiť ich/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeDisabled();
  });

  it("drops the unsaved-pin note once the tree is committed (the commit saved the pin)", async () => {
    upgradeNexsharedApiMock.mockResolvedValue({ upgraded: true, target_version: "0.19.0", committed: false });
    getGitStatusApiMock.mockResolvedValueOnce(cleanTree).mockResolvedValueOnce(dirtyTree).mockResolvedValue(cleanTree);

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));
    expect(await screen.findByText(/nepodarilo sa ho uložiť/i)).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: /uložiť ich/i }));

    await waitFor(() => expect(screen.queryByText(/nepodarilo sa ho uložiť/i)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /uložiť zadanie/i })).toBeEnabled();
  });

  it("dismisses the prompt only when the pin was really committed", async () => {
    upgradeNexsharedApiMock.mockResolvedValue({ upgraded: true, target_version: "0.19.0", committed: true });

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));

    await waitFor(() => expect(screen.queryByText(/K dispozícii je novší nex-shared/i)).not.toBeInTheDocument());
    expect(screen.queryByText(/nepodarilo sa ho uložiť/i)).not.toBeInTheDocument();
    // Even the happy path re-reads the tree — the commit could have left something behind.
    await waitFor(() => expect(getGitStatusApiMock).toHaveBeenCalledTimes(2));
  });

  it("renders a rejected upgrade instead of swallowing it", async () => {
    upgradeNexsharedApiMock.mockRejectedValue(new ApiError(400, "chýba nex-shared pin"));

    await renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /povýšiť na v0\.19\.0/i }));

    expect(await screen.findByText(/Povýšenie nex-shared na v0\.19\.0 zlyhalo/i)).toBeInTheDocument();
    // Still offered — the Manažér can retry or stay.
    expect(screen.getByRole("button", { name: /povýšiť na v0\.19\.0/i })).toBeEnabled();
  });
});
