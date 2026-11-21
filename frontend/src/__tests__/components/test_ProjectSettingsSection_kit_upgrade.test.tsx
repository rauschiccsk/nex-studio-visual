/**
 * DEV-417 — a project behind the template's newest release is offered the upgrade in its settings, whatever its
 * languages need. 17.11.2026 NEX Manager 1.7.0 (already in Príprava) had to move to nex-app-kit 0.16.0, but the
 * settings offered the upgrade only when the languages needed it, and the other offer stands only where a version is
 * founded — the Manažér: „Ja nevidím možnosť povýšiť“. Director: „Súhlasím“.
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
  name: "NEX Manager",
  slug: "nex-manager",
  type: "standard",
  auth_mode: "password",
  description: "",
  status: "active",
  backend_port: 10210,
  frontend_port: 10211,
  db_port: 10212,
  repo_url: null,
  source_path: "/opt/projects/nex-manager",
  kb_path: null,
  guardian_enabled: false,
  setup_warnings: [],
  custom_development_enabled: false,
  private_network: true,
  template: "nsv-standard",
  languages: ["sk", "hu", "en"],
  created_by: "u1",
} as unknown as ProjectRead;

const OPTIONS = [
  { code: "sk", label: "slovenčina" },
  { code: "hu", label: "maďarčina" },
  { code: "en", label: "angličtina" },
];
const SINCE = { sk: "0.14.1", hu: "0.14.1", en: "0.15.0" };
const NEWS = [{ version: "0.16.0", body: "Správca mení jazyk človeka v Používateľoch." }];

function kit(current: string, extra: object = {}) {
  const behind = current === "0.16.0" ? 0 : 1;
  const known = current >= "0.15.0" ? ["sk", "hu", "en"] : ["sk", "hu"];
  return {
    current,
    latest: "0.16.0",
    behind,
    up_to_date: behind === 0,
    changelog: behind ? NEWS : [],
    parts: [],
    languages_known: known,
    languages_from: SINCE,
    uncommitted: null,
    ...extra,
  };
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.getProjectCreatePresetsApi.mockResolvedValue({ languages: OPTIONS, default_languages: ["sk"] });
  api.upgradeAppKitApi.mockResolvedValue({ upgraded: true, target_version: "0.16.0", committed: true });
});

function open(project: ProjectRead = PROJECT, onUpgraded?: () => void) {
  render(<ProjectSettingsSection project={project} canEdit onSaved={vi.fn()} onUpgraded={onUpgraded} />);
  fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
}

describe("ProjectSettingsSection — the template's newest release (DEV-417)", () => {
  it("a project whose languages need nothing newer is offered the upgrade, with what the release brings", async () => {
    api.getAppKitStatusApi.mockResolvedValueOnce(kit("0.15.2")).mockResolvedValueOnce(kit("0.16.0"));
    open();

    const offer = await screen.findByTestId("kit-upgrade");
    expect(offer).toHaveTextContent("K dispozícii je novšia šablóna aplikácie");
    expect(offer).toHaveTextContent("Správca mení jazyk človeka v Používateľoch.");
    expect(screen.queryByTestId("kit-too-old")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Povýšiť na v0.16.0" }));

    await waitFor(() => expect(api.upgradeAppKitApi).toHaveBeenCalledWith("p1", "0.16.0"));
    await waitFor(() => expect(screen.queryByTestId("kit-upgrade")).not.toBeInTheDocument());
  });

  it("the Manažér may stay on the current release", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.15.2"));
    open();

    fireEvent.click(await screen.findByRole("button", { name: "Ostať na v0.15.2" }));

    expect(screen.queryByTestId("kit-upgrade")).not.toBeInTheDocument();
    expect(api.upgradeAppKitApi).not.toHaveBeenCalled();
  });

  it("nothing is offered to a project on the newest release", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.16.0"));
    open();

    await waitFor(() => expect(api.getAppKitStatusApi).toHaveBeenCalled());
    expect(screen.queryByTestId("kit-upgrade")).not.toBeInTheDocument();
  });

  it("when the languages need the newer release, their own note carries the one upgrade button", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.14.1"));
    open();

    expect(await screen.findByTestId("kit-too-old")).toBeInTheDocument();
    expect(screen.queryByTestId("kit-upgrade")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Povýšiť/ })).toHaveLength(1);
  });

  it("an upgrade left unsaved is saved first — no second offer beside it", async () => {
    api.getAppKitStatusApi.mockResolvedValue(kit("0.15.2", { uncommitted: "0.16.0" }));
    open();

    expect(await screen.findByTestId("kit-unsaved")).toBeInTheDocument();
    expect(screen.queryByTestId("kit-upgrade")).not.toBeInTheDocument();
  });
});

describe("ProjectSettingsSection — the page hears of the upgrade (DEV-452)", () => {
  it("after the upgrade the page is told, so the offer under Zadanie od Deda reads the new state", async () => {
    api.getAppKitStatusApi.mockResolvedValueOnce(kit("0.15.2")).mockResolvedValueOnce(kit("0.16.0"));
    const onUpgraded = vi.fn();
    open(PROJECT, onUpgraded);

    fireEvent.click(await screen.findByRole("button", { name: "Povýšiť na v0.16.0" }));

    await waitFor(() => expect(onUpgraded).toHaveBeenCalledTimes(1));
    expect(api.getAppKitStatusApi.mock.invocationCallOrder[1]).toBeLessThan(onUpgraded.mock.invocationCallOrder[0] ?? 0);
  });

  it("saving an unsaved upgrade tells the page too", async () => {
    api.getAppKitStatusApi
      .mockResolvedValueOnce(kit("0.15.2", { uncommitted: "0.16.0" }))
      .mockResolvedValueOnce(kit("0.16.0"));
    api.commitAppKitApi.mockResolvedValue({ committed: true });
    const onUpgraded = vi.fn();
    open(PROJECT, onUpgraded);

    const note = await screen.findByTestId("kit-unsaved");
    fireEvent.click(within(note).getByRole("button"));

    await waitFor(() => expect(onUpgraded).toHaveBeenCalledTimes(1));
  });
});
