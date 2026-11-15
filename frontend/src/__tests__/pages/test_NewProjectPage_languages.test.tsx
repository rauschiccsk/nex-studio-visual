/**
 * DEV-395 — the new-project form offers the languages of the app with the template: the main one and the others; the
 * project is founded speaking them. Without the template the app speaks Slovak and the choice is not offered.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  createProjectApi: vi.fn(),
  suggestPortBlockApi: vi.fn(),
  getProjectCreatePresetsApi: vi.fn(),
}));
vi.mock("@/services/api/projects", () => api);
vi.mock("@/services/api/systemSettings", () => ({
  getSystemSettingApi: vi.fn().mockResolvedValue({ key: "github_org", value: "rauschiccsk" }),
}));
vi.mock("@/services/api/users", () => ({ listUsersApi: vi.fn().mockRejectedValue(new Error("403")) }));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ user: { id: "u-alex", username: "alex", first_name: "Alexander", last_name: "Honest", role: "ri" } }),
}));

import NewProjectPage from "@/pages/NewProjectPage";

const PRESETS = {
  enable_cicd: true,
  full_smoke: true,
  enable_branch_protection: false,
  custom_development_enabled: false,
  private_network: false,
  branch_protection_available: true,
  branch_protection_note: null,
  web_available: true,
  web_note: null,
  templates: [{ id: "nsv-standard", label: "NSV štandard", description: "Rám zo šablóny." }],
  default_template: "nsv-standard",
  languages: [
    { code: "sk", label: "slovenčina" },
    { code: "hu", label: "maďarčina" },
  ],
  default_languages: ["sk"],
};

function renderForm() {
  return render(
    <MemoryRouter>
      <NewProjectPage />
    </MemoryRouter>,
  );
}

async function create() {
  fireEvent.change(screen.getByPlaceholderText("NEX Ledger"), { target: { value: "Orthodox Hermenia" } });
  fireEvent.click(screen.getByRole("button", { name: /Vytvoriť projekt/ }));
  await waitFor(() => expect(api.createProjectApi).toHaveBeenCalledTimes(1));
  return api.createProjectApi.mock.calls[0]![0];
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.suggestPortBlockApi.mockResolvedValue({ base: 10300, block_size: 10, warnings: [] });
  api.createProjectApi.mockResolvedValue({ slug: "orthodox-hermenia", repo_url: null, setup_warnings: [] });
  api.getProjectCreatePresetsApi.mockResolvedValue(PRESETS);
});

describe("DEV-395 — „Jazyky aplikácie“ when founding a project", () => {
  it("starts Slovak and founds the project speaking the languages chosen, the main one first", async () => {
    renderForm();
    const main = (await screen.findByRole("combobox", { name: /Hlavný jazyk/ })) as HTMLSelectElement;
    expect(main.value).toBe("sk");

    fireEvent.change(main, { target: { value: "hu" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "slovenčina" }));

    expect(await create()).toMatchObject({ template: "nsv-standard", languages: ["hu", "sk"] });
  });

  it("a new project starts speaking Slovak, Hungarian and English when the cockpit says so (DEV-399)", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue({
      ...PRESETS,
      languages: [...PRESETS.languages, { code: "en", label: "angličtina" }],
      default_languages: ["sk", "hu", "en"],
    });
    renderForm();

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "angličtina" })).toBeChecked());
    expect((screen.getByRole("combobox", { name: /Hlavný jazyk/ }) as HTMLSelectElement).value).toBe("sk");
    expect(screen.getByRole("checkbox", { name: "maďarčina" })).toBeChecked();
    expect(await create()).toMatchObject({ template: "nsv-standard", languages: ["sk", "hu", "en"] });
  });

  it("the main language is never also one of the others", async () => {
    renderForm();
    fireEvent.click(await screen.findByRole("checkbox", { name: "maďarčina" }));
    fireEvent.change(screen.getByRole("combobox", { name: /Hlavný jazyk/ }), { target: { value: "hu" } });

    expect(screen.queryByRole("checkbox", { name: "maďarčina" })).not.toBeInTheDocument();
    expect(await create()).toMatchObject({ languages: ["hu"] });
  });

  it("without the template the choice is gone and the app speaks Slovak", async () => {
    renderForm();
    fireEvent.change(await screen.findByRole("combobox", { name: /Hlavný jazyk/ }), { target: { value: "hu" } });
    fireEvent.click(screen.getByRole("button", { name: /^Bez šablóny/ }));

    expect(screen.queryByTestId("app-languages")).not.toBeInTheDocument();
    expect(await create()).toMatchObject({ template: null, languages: ["sk"] });
  });
});
