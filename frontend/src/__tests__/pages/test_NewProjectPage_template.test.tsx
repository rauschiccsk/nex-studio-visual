/**
 * DEV-253 — the new-project form offers the app templates the backend lists, NSV štandard first.
 *
 * Director 28.10.2026: „pri každom novom projekte by som mal možnosť vybrať vytvoriť vizuál novej aplikácie podľa
 * nejakého šablónu. Prvú takú šablónu by som chcel spraviť z NSV“. The template gives the frame, sign-in,
 * Nastavenia and server; „AI agentov“ adds Nastavenia → Agenti and is chosen only together with a template. A Web
 * project is founded without one — the backend refuses the pair by the same rule.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const DESCRIPTION = "Ľavý panel, prihlásenie, Nastavenia a server ako v NSV — agent stavia len obrazovky aplikácie.";
const WEB_ONLY_WITHOUT = "Šablóna je zatiaľ len pre štandardný projekt — Web projekt sa zakladá bez nej.";

function presets(withTemplates = true) {
  return {
    enable_cicd: true,
    full_smoke: true,
    enable_branch_protection: false,
    custom_development_enabled: false,
    private_network: false,
    branch_protection_available: true,
    branch_protection_note: null,
    web_available: true,
    web_note: null,
    ...(withTemplates
      ? {
          templates: [{ id: "nsv-standard", label: "NSV štandard", description: DESCRIPTION }],
          default_template: "nsv-standard",
        }
      : {}),
  };
}

const nsv = () => screen.getByRole("button", { name: /^NSV štandard/ });
const none = () => screen.getByRole("button", { name: /^Bez šablóny/ });
const aiBox = () => screen.queryByRole("checkbox", { name: /Aplikácia používa AI agentov/ });
const web = () => screen.getByRole("button", { name: /^Web/ });

function renderForm() {
  return render(
    <MemoryRouter>
      <NewProjectPage />
    </MemoryRouter>,
  );
}

async function create() {
  fireEvent.change(screen.getByPlaceholderText("NEX Ledger"), { target: { value: "NEX Knowledge" } });
  fireEvent.click(screen.getByRole("button", { name: /Vytvoriť projekt/ }));
  await waitFor(() => expect(api.createProjectApi).toHaveBeenCalledTimes(1));
  return api.createProjectApi.mock.calls[0]![0];
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.suggestPortBlockApi.mockResolvedValue({ base: 10260, block_size: 10, warnings: [] });
  api.createProjectApi.mockResolvedValue({ slug: "nex-knowledge", repo_url: null, setup_warnings: [] });
});

describe("DEV-253 — „Šablóna aplikácie“ when founding a project", () => {
  it("offers NSV štandard first, says what it gives, and founds on it without AI unless ticked", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets());
    renderForm();

    await waitFor(() => expect(nsv()).toHaveAttribute("aria-pressed", "true"));
    expect(none()).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(DESCRIPTION)).toBeInTheDocument();
    expect(aiBox()).not.toBeChecked();

    expect(await create()).toMatchObject({ type: "standard", template: "nsv-standard", ai: false });
  });

  it("ticked AI agents go with the template", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets());
    renderForm();
    await waitFor(() => expect(aiBox()).toBeInTheDocument());

    fireEvent.click(aiBox()!);

    expect(await create()).toMatchObject({ template: "nsv-standard", ai: true });
  });

  it("„Bez šablóny“ founds as before — no template, and no AI agents even if they were ticked", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets());
    renderForm();
    await waitFor(() => expect(aiBox()).toBeInTheDocument());

    fireEvent.click(aiBox()!);
    fireEvent.click(none());

    expect(none()).toHaveAttribute("aria-pressed", "true");
    expect(aiBox()).not.toBeInTheDocument();
    expect(await create()).toMatchObject({ template: null, ai: false });
  });

  it("a Web project is founded without a template, and the form says why", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets());
    renderForm();
    await waitFor(() => expect(web()).toBeEnabled());

    fireEvent.click(web());

    expect(screen.getByText(WEB_ONLY_WITHOUT)).toBeInTheDocument();
    expect(nsv()).toBeDisabled();
    expect(nsv()).toHaveAttribute("aria-pressed", "false");
    expect(none()).toHaveAttribute("aria-pressed", "true");
    expect(aiBox()).not.toBeInTheDocument();
    expect(await create()).toMatchObject({ type: "web", template: null, ai: false });
  });

  it("back on Štandardný, the template chosen before is chosen again", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets());
    renderForm();
    await waitFor(() => expect(web()).toBeEnabled());

    fireEvent.click(web());
    fireEvent.click(screen.getByRole("button", { name: /^Štandardný/ }));

    expect(screen.queryByText(WEB_ONLY_WITHOUT)).not.toBeInTheDocument();
    expect(await create()).toMatchObject({ type: "standard", template: "nsv-standard" });
  });

  it("an option changed before the presets arrived does not cost the template", async () => {
    let arrive: (v: ReturnType<typeof presets>) => void = () => {};
    api.getProjectCreatePresetsApi.mockReturnValue(new Promise((r) => (arrive = r)));
    renderForm();

    fireEvent.click(screen.getByRole("checkbox", { name: /Úplná kontrola po zostavení/ }));
    await act(async () => arrive(presets()));

    expect(nsv()).toHaveAttribute("aria-pressed", "true");
    expect(await create()).toMatchObject({ template: "nsv-standard" });
  });

  it("with no templates listed there is no choice, and the project is founded without one", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets(false));
    renderForm();
    await waitFor(() => expect(web()).toBeEnabled());

    expect(screen.queryByText("Šablóna aplikácie")).not.toBeInTheDocument();
    expect(await create()).toMatchObject({ template: null, ai: false });
  });
});
