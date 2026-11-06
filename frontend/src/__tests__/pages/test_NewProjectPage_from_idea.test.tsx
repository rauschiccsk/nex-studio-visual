/**
 * DEV-318 — the form Nový projekt opened from an idea (Nápady) is prefilled from the brief Poradca wrote; the person
 * checks it and confirms, and the idea then learns where it ended (the brief into 0.1.0, the project's ticket).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

const api = vi.hoisted(() => ({
  createProjectApi: vi.fn(),
  suggestPortBlockApi: vi.fn(),
  getProjectCreatePresetsApi: vi.fn(),
}));
const ideas = vi.hoisted(() => ({ getIdeaApi: vi.fn(), ideaFoundedApi: vi.fn() }));
vi.mock("@/services/api/projects", () => api);
vi.mock("@/services/api/ideas", () => ideas);
vi.mock("@/services/api/systemSettings", () => ({
  getSystemSettingApi: vi.fn().mockResolvedValue({ key: "github_org", value: "rauschiccsk" }),
}));
vi.mock("@/services/api/users", () => ({ listUsersApi: vi.fn().mockRejectedValue(new Error("403")) }));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: "u-z", username: "zoltan", role: "ri" } }),
}));

import NewProjectPage from "@/pages/NewProjectPage";

const BRIEF = {
  name: "Orthodox Register",
  slug: "orthodox-register",
  description: "Pravoslávna matrika",
  type: "standard",
  auth_mode: "password",
  template: "",
  ai: false,
  work_kind: "change",
  brief: "## Prečo",
};

function Landed() {
  const where = useLocation();
  return <div data-testid="landed">{JSON.stringify({ path: where.pathname, fromIdea: (where.state as { fromIdea?: unknown })?.fromIdea })}</div>;
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  Object.values(ideas).forEach((f) => f.mockReset());
  api.suggestPortBlockApi.mockResolvedValue({ base: 10260, block_size: 10, warnings: [] });
  api.getProjectCreatePresetsApi.mockResolvedValue({
    enable_cicd: true,
    full_smoke: true,
    enable_branch_protection: false,
    custom_development_enabled: false,
    private_network: false,
    branch_protection_available: true,
    branch_protection_note: null,
    web_available: true,
    web_note: null,
    templates: [{ id: "nsv-standard", label: "NSV štandard", description: "d" }],
    default_template: "nsv-standard",
  });
  api.createProjectApi.mockResolvedValue({ id: "p-new", slug: "orthodox-register", repo_url: null, setup_warnings: [] });
  ideas.getIdeaApi.mockResolvedValue({
    id: "i1",
    title: "Matrika",
    conversation: { messages: [{ id: "m1", author: "poradca", idea_project: BRIEF }] },
  });
  ideas.ideaFoundedApi.mockResolvedValue({ plane_identifier: "DEV-400", plane_url: "https://plane/DEV-400", ticket_error: null });
});

describe("Nový projekt from an idea (DEV-318)", () => {
  it("is prefilled from the brief, keeps its no-template choice, and tells the idea after founding", async () => {
    render(
      <MemoryRouter initialEntries={["/projects/new?napad=i1&odpoved=m1"]}>
        <Routes>
          <Route path="/projects/new" element={<NewProjectPage />} />
          <Route path="*" element={<Landed />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/vyplnený zo zadania nápadu „Matrika“/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByDisplayValue("Orthodox Register")).toBeInTheDocument());
    expect(screen.getByDisplayValue("orthodox-register")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Pravoslávna matrika")).toBeInTheDocument();
    await waitFor(() => expect(api.getProjectCreatePresetsApi).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /Vytvoriť projekt/ }));

    await waitFor(() => expect(api.createProjectApi).toHaveBeenCalledTimes(1));
    const sent = api.createProjectApi.mock.calls[0]![0];
    expect([sent.name, sent.slug, sent.description, sent.type, sent.auth_mode, sent.template]).toEqual([
      "Orthodox Register",
      "orthodox-register",
      "Pravoslávna matrika",
      "standard",
      "password",
      null,
    ]);
    await waitFor(() => expect(ideas.ideaFoundedApi).toHaveBeenCalledWith("i1", "m1", "p-new"));
    const landed = JSON.parse((await screen.findByTestId("landed")).textContent ?? "{}");
    expect(landed.path).toBe("/projects/orthodox-register");
    expect(landed.fromIdea).toEqual({ ideaId: "i1", title: "Matrika", ticket: "DEV-400", ticketUrl: "https://plane/DEV-400", problem: null });
  });
});
