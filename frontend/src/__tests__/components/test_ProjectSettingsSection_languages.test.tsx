/**
 * DEV-395 — the languages of an app on the template in the project's settings: shown, changed, and an older
 * nex-app-kit upgraded right there before the languages can be saved.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const api = vi.hoisted(() => ({
  updateProjectApi: vi.fn(),
  getProjectCreatePresetsApi: vi.fn(),
  getAppKitStatusApi: vi.fn(),
  upgradeAppKitApi: vi.fn(),
}));
vi.mock("@/services/api/projects", () => api);

import ProjectSettingsSection from "@/components/project/ProjectSettingsSection";
import type { ProjectRead } from "@/types";

const PROJECT = {
  id: "p1",
  name: "Orthodox Hermenia",
  slug: "orthodox-hermenia",
  type: "standard",
  auth_mode: "password",
  description: "",
  status: "active",
  backend_port: 10300,
  frontend_port: 10301,
  db_port: 10302,
  repo_url: null,
  source_path: "/opt/projects/orthodox-hermenia",
  kb_path: null,
  guardian_enabled: false,
  setup_warnings: [],
  custom_development_enabled: false,
  private_network: true,
  template: "nsv-standard",
  languages: ["sk"],
  created_by: "u1",
} as unknown as ProjectRead;

const OPTIONS = [
  { code: "sk", label: "slovenčina" },
  { code: "hu", label: "maďarčina" },
];

function kit(current: string, speaks: boolean) {
  return { current, latest: "0.14.1", behind: speaks ? 0 : 1, up_to_date: speaks, changelog: [], parts: [], speaks_languages: speaks, languages_from: "0.14.1" };
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.getProjectCreatePresetsApi.mockResolvedValue({ languages: OPTIONS, default_languages: ["sk"] });
  api.updateProjectApi.mockImplementation(async (_id: string, payload: object) => ({ ...PROJECT, ...payload }));
});

function edit(project: ProjectRead = PROJECT) {
  const onSaved = vi.fn();
  render(<ProjectSettingsSection project={project} canEdit onSaved={onSaved} />);
  return { onSaved };
}

describe("ProjectSettingsSection — languages (DEV-395)", () => {
  it("names the app's languages, the main one first", async () => {
    edit({ ...PROJECT, languages: ["hu", "sk"] } as ProjectRead);

    const block = await screen.findByTestId("project-languages");
    expect(within(block).getByText("maďarčina (hlavný jazyk), slovenčina")).toBeInTheDocument();
  });

  it("is not offered for a project without the template", async () => {
    edit({ ...PROJECT, template: null } as ProjectRead);

    await waitFor(() => expect(screen.getByLabelText("Názov")).toBeInTheDocument());
    expect(screen.queryByTestId("project-languages")).not.toBeInTheDocument();
    expect(api.getProjectCreatePresetsApi).not.toHaveBeenCalled();
  });

  it("saves the languages chosen — the main one first", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.14.1", true));
    edit();
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "hu" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "slovenčina" }));
    await waitFor(() => expect(api.getAppKitStatusApi).toHaveBeenCalledWith("p1"));
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(api.updateProjectApi).toHaveBeenCalledWith("p1", { languages: ["hu", "sk"] }));
  });

  it("an older kit is upgraded first — saving waits for it", async () => {
    api.getAppKitStatusApi.mockResolvedValueOnce(kit("0.13.0", false)).mockResolvedValueOnce(kit("0.14.1", true));
    api.upgradeAppKitApi.mockResolvedValue({ upgraded: true, target_version: "0.14.1", committed: true });
    edit();
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "maďarčina" }));

    const note = await screen.findByTestId("kit-too-old");
    expect(note).toHaveTextContent("Jazyky pozná šablóna aplikácie od verzie nex-app-kit 0.14.1, projekt stojí na 0.13.0.");
    expect(screen.getByRole("button", { name: /Uložiť/ })).toBeDisabled();

    fireEvent.click(within(note).getByRole("button", { name: "Povýšiť šablónu na 0.14.1" }));

    await waitFor(() => expect(api.upgradeAppKitApi).toHaveBeenCalledWith("p1", "0.14.1"));
    await waitFor(() => expect(screen.queryByTestId("kit-too-old")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Uložiť/ })).toBeEnabled();
  });

  it("a refused upgrade is said and nothing is saved", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.13.0", false));
    api.upgradeAppKitApi.mockRejectedValue(new Error("Agent práve pracuje na verzii 0.1.0"));
    edit();
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "maďarčina" }));
    fireEvent.click(within(await screen.findByTestId("kit-too-old")).getByRole("button", { name: /Povýšiť šablónu/ }));

    await waitFor(() => expect(screen.getByText(/Šablónu aplikácie sa nepodarilo povýšiť/)).toBeInTheDocument());
    expect(api.updateProjectApi).not.toHaveBeenCalled();
  });
});
