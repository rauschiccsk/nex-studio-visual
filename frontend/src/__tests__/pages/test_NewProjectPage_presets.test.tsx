/**
 * DEV-37 — the new-project form starts with the options the Manažér always ticks.
 *
 * Director 09.10.2026, creating NEX Career: „V dolnej časti je "Možnosti nastavenia" vždy musíme nastavovať. Chcem
 * aby pri založení nového projektu už tie voľby boli prednastavené." Approved: CI and the full check ON; branch
 * protection OFF and greyed out with the reason while GitHub refuses it on a private repository; „Vývoj na
 * zákazku" unchanged. The presets come from the backend; the form never overrides what he already changed.
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

const NOTE =
  "Ochranu hlavnej vetvy GitHub pri súkromnom repozitári na našom pláne nedovolí (treba GitHub Pro). Keď sa plán " +
  "zmení, zapni ju v Nastavenia → GitHub → „Ochrana vetvy pri súkromnom repozitári“.";

function presets(available: boolean) {
  return {
    enable_cicd: true,
    full_smoke: true,
    enable_branch_protection: false,
    custom_development_enabled: false,
    private_network: false,
    branch_protection_available: available,
    branch_protection_note: available ? null : NOTE,
  };
}

const ci = () => screen.getByRole("checkbox", { name: /Automaticky kontrolovať a zostaviť/ });
const smoke = () => screen.getByRole("checkbox", { name: /Úplná kontrola po zostavení/ });
const protection = () => screen.getByRole("checkbox", { name: /Chrániť hlavnú vetvu/ });
const custom = () => screen.getByRole("checkbox", { name: /Vývoj na zákazku/ });

function renderForm() {
  return render(
    <MemoryRouter>
      <NewProjectPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.suggestPortBlockApi.mockResolvedValue({ base: 10250, block_size: 10, warnings: [] });
  api.createProjectApi.mockResolvedValue({ slug: "nex-career", repo_url: null, setup_warnings: [] });
});

describe("DEV-37 — „Možnosti nastavenia“ start preset", () => {
  it("CI and the full check are ticked; branch protection is off, greyed out, and says why", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets(false));
    renderForm();

    await waitFor(() => expect(ci()).toBeChecked());
    expect(smoke()).toBeChecked();
    expect(protection()).not.toBeChecked();
    expect(protection()).toBeDisabled();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    expect(custom()).not.toBeChecked();
  });

  it("once the plan allows it, branch protection can be ticked — but is not ticked for him", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets(true));
    renderForm();

    await waitFor(() => expect(ci()).toBeChecked());
    expect(protection()).toBeEnabled();
    expect(protection()).not.toBeChecked();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it("creating without touching the options sends the presets", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets(false));
    renderForm();
    await waitFor(() => expect(ci()).toBeChecked());

    fireEvent.change(screen.getByPlaceholderText("NEX Ledger"), { target: { value: "NEX Career" } });
    fireEvent.click(screen.getByRole("button", { name: /Vytvoriť projekt/ }));

    await waitFor(() => expect(api.createProjectApi).toHaveBeenCalledTimes(1));
    expect(api.createProjectApi.mock.calls[0]![0]).toMatchObject({
      slug: "nex-career",
      enable_cicd: true,
      full_smoke: true,
      enable_branch_protection: false,
      custom_development_enabled: false,
    });
  });

  it("what he changed before the presets arrived stays as he left it", async () => {
    let arrive: (v: ReturnType<typeof presets>) => void = () => {};
    api.getProjectCreatePresetsApi.mockReturnValue(new Promise((r) => (arrive = r)));
    renderForm();

    fireEvent.click(smoke()); // on …
    fireEvent.click(smoke()); // … and off again: he does not want the full check this time
    await act(async () => arrive(presets(false)));

    expect(smoke()).not.toBeChecked();
    expect(ci()).not.toBeChecked();
  });

  it("presets that cannot be loaded leave the options unticked and say so", async () => {
    api.getProjectCreatePresetsApi.mockRejectedValue(new Error("sieť"));
    renderForm();

    expect(
      await screen.findByText("Predvolené možnosti sa nepodarilo načítať — zaškrtni, čo chceš, sám."),
    ).toBeInTheDocument();
    expect(ci()).not.toBeChecked();
    expect(smoke()).not.toBeChecked();
  });
});

describe("DEV-42 — „Prístup len zo súkromnej siete“ when founding a project", () => {
  const privateNet = () => screen.getByRole("checkbox", { name: /Prístup len zo súkromnej siete \(Tailscale\)/ });

  it("is offered switched off, says what it means, and when ticked it is sent with the project", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets(false));
    renderForm();
    await waitFor(() => expect(ci()).toBeChecked());

    expect(privateNet()).not.toBeChecked();
    expect(screen.getByText(/dostupnú len zo zariadení v Tailscale — verejnú adresu nedostanú vôbec/)).toBeInTheDocument();
    fireEvent.click(privateNet());
    fireEvent.change(screen.getByPlaceholderText("NEX Ledger"), { target: { value: "Career Asistent" } });
    fireEvent.click(screen.getByRole("button", { name: /Vytvoriť projekt/ }));

    await waitFor(() => expect(api.createProjectApi).toHaveBeenCalledTimes(1));
    expect(api.createProjectApi.mock.calls[0]![0]).toMatchObject({ private_network: true });
  });

  it("left alone it is sent off — a project stays public as before", async () => {
    api.getProjectCreatePresetsApi.mockResolvedValue(presets(false));
    renderForm();
    await waitFor(() => expect(ci()).toBeChecked());

    fireEvent.change(screen.getByPlaceholderText("NEX Ledger"), { target: { value: "NEX Career" } });
    fireEvent.click(screen.getByRole("button", { name: /Vytvoriť projekt/ }));

    await waitFor(() => expect(api.createProjectApi).toHaveBeenCalledTimes(1));
    expect(api.createProjectApi.mock.calls[0]![0]).toMatchObject({ private_network: false });
  });
});

