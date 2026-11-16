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
  commitAppKitApi: vi.fn(),
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
  { code: "en", label: "angličtina" },
];

const SINCE = { sk: "0.14.1", hu: "0.14.1", en: "0.15.0" };

/** The kit status as the backend says it: the languages the project's release speaks. */
function kit(current: string) {
  const known = current === "0.15.0" ? ["sk", "hu", "en"] : current === "0.14.1" ? ["sk", "hu"] : [];
  return { current, latest: "0.15.0", behind: current === "0.15.0" ? 0 : 1, up_to_date: current === "0.15.0", changelog: [], parts: [], languages_known: known, languages_from: SINCE };
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
    api.getAppKitStatusApi.mockResolvedValue(kit("0.15.0"));
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
    api.getAppKitStatusApi.mockResolvedValueOnce(kit("0.13.0")).mockResolvedValueOnce(kit("0.15.0"));
    api.upgradeAppKitApi.mockResolvedValue({ upgraded: true, target_version: "0.14.1", committed: true });
    edit();
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "maďarčina" }));

    const note = await screen.findByTestId("kit-too-old");
    expect(note).toHaveTextContent("Jazyky slovenčina, maďarčina pozná šablóna aplikácie od verzie nex-app-kit 0.14.1, projekt stojí na 0.13.0.");
    expect(screen.getByRole("button", { name: /Uložiť/ })).toBeDisabled();

    fireEvent.click(within(note).getByRole("button", { name: "Povýšiť šablónu na 0.15.0" }));

    await waitFor(() => expect(api.upgradeAppKitApi).toHaveBeenCalledWith("p1", "0.15.0"));
    await waitFor(() => expect(screen.queryByTestId("kit-too-old")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Uložiť/ })).toBeEnabled();
  });

  it("English needs a newer kit than Hungarian — the note names the release English comes with (DEV-399)", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.14.1"));
    edit({ ...PROJECT, languages: ["sk", "hu"] } as ProjectRead);
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    await waitFor(() => expect(api.getAppKitStatusApi).toHaveBeenCalled());
    expect(screen.queryByTestId("kit-too-old")).not.toBeInTheDocument(); // Slovak and Hungarian are known to 0.14.1

    fireEvent.click(screen.getByRole("checkbox", { name: "angličtina" }));

    expect(await screen.findByTestId("kit-too-old")).toHaveTextContent(
      "Jazyky slovenčina, maďarčina, angličtina pozná šablóna aplikácie od verzie nex-app-kit 0.15.0, projekt stojí na 0.14.1.",
    );
  });

  it("an upgrade left unsaved in the files is said and saved with one click (DEV-400)", async () => {
    api.getAppKitStatusApi
      .mockResolvedValueOnce({ ...kit("0.15.0"), uncommitted: "0.15.0" })
      .mockResolvedValueOnce({ ...kit("0.15.0"), uncommitted: null });
    api.commitAppKitApi.mockResolvedValue({ upgraded: true, target_version: "0.15.0", committed: true });
    edit({ ...PROJECT, languages: ["sk", "hu", "en"] } as ProjectRead);
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));

    const note = await screen.findByTestId("kit-unsaved");
    expect(note).toHaveTextContent("Šablóna aplikácie je v súboroch projektu povýšená na 0.15.0, ale v gite projektu nie je uložená");
    fireEvent.click(within(note).getByRole("button", { name: "Uložiť do gitu" }));

    await waitFor(() => expect(api.commitAppKitApi).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByTestId("kit-unsaved")).not.toBeInTheDocument());
  });

  it("an upgrade whose commit failed shows the unsaved note at once (DEV-400)", async () => {
    api.getAppKitStatusApi
      .mockResolvedValueOnce(kit("0.13.0"))
      .mockResolvedValueOnce({ ...kit("0.15.0"), uncommitted: "0.15.0" });
    api.upgradeAppKitApi.mockResolvedValue({ upgraded: true, target_version: "0.15.0", committed: false });
    edit();
    await screen.findByTestId("project-languages");
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "maďarčina" }));
    fireEvent.click(within(await screen.findByTestId("kit-too-old")).getByRole("button", { name: /Povýšiť šablónu/ }));

    expect(await screen.findByTestId("kit-unsaved")).toHaveTextContent("povýšená na 0.15.0");
  });

  it("a refused upgrade is said and nothing is saved", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.13.0"));
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
