/**
 * Nastavenia projektu (ICCINT-7) — the only in-product way to correct a founded project.
 *
 * Before this section existed a value typed at creation was final: on 21.08.2026 a project
 * recorded on another system's reserved port block had to be repaired directly in the
 * database, because the cockpit offered no other path.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { updateProjectApi } = vi.hoisted(() => ({ updateProjectApi: vi.fn() }));
vi.mock("@/services/api/projects", () => ({ updateProjectApi }));

import ProjectSettingsSection from "@/components/project/ProjectSettingsSection";
import type { ProjectRead } from "@/types";

const PROJECT = {
  id: "p1",
  name: "NEX ProductCatalogs",
  slug: "nex-productcatalogs",
  type: "standard",
  auth_mode: "token",
  description: "Produktové katalógy",
  status: "active",
  backend_port: 10190,
  frontend_port: 10191,
  db_port: 10192,
  repo_url: "https://github.com/rauschiccsk/nex-productcatalogs",
  source_path: "/opt/projects/nex-productcatalogs",
  kb_path: null,
  guardian_enabled: false,
  setup_warnings: [],
  custom_development_enabled: false,
  created_by: "u1",
} as unknown as ProjectRead;

function renderSection(overrides: Partial<React.ComponentProps<typeof ProjectSettingsSection>> = {}) {
  const onSaved = vi.fn();
  render(
    <ProjectSettingsSection project={PROJECT} canEdit onSaved={onSaved} {...overrides} />,
  );
  return { onSaved };
}

beforeEach(() => {
  updateProjectApi.mockReset();
  updateProjectApi.mockResolvedValue({ ...PROJECT, setup_warnings: [] });
});

describe("ProjectSettingsSection", () => {
  it("reads first — fields are disabled until Upraviť is pressed", () => {
    renderSection();
    expect(screen.getByLabelText("Názov")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Uložiť/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    expect(screen.getByLabelText("Názov")).toBeEnabled();
    expect(screen.getByRole("button", { name: /Uložiť/ })).toBeInTheDocument();
  });

  it("sends ONLY what changed", async () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.change(screen.getByLabelText("Popis"), { target: { value: "nový popis" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(updateProjectApi).toHaveBeenCalledTimes(1));
    // Re-sending every field would make the port checks run on an edit that never
    // touched a port — and those checks probe Docker.
    expect(updateProjectApi).toHaveBeenCalledWith("p1", { description: "nový popis" });
  });

  it("saves nothing when nothing changed", async () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Uložiť/ })).not.toBeInTheDocument(),
    );
    expect(updateProjectApi).not.toHaveBeenCalled();
  });

  it("warns that a port change does not redeploy anything — but only when a port changed", () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    expect(screen.queryByText(/neprenasadí nič/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Backend"), { target: { value: "10230" } });
    expect(screen.getByText(/Zmena portu neprenasadí nič/)).toBeInTheDocument();
  });

  it("says the slug stays when the project is renamed", () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.change(screen.getByLabelText("Názov"), { target: { value: "Iný názov" } });
    expect(screen.getByText(/nechajú pôvodný/)).toBeInTheDocument();
  });

  it("shows a failed registry write-back instead of swallowing it", async () => {
    const warning = "Blok 10230-10239 sa nepodarilo zapísať do evidencie portov. Dopíš ho ručne.";
    updateProjectApi.mockResolvedValue({ ...PROJECT, setup_warnings: [warning] });
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: /Upraviť/ }));
    fireEvent.change(screen.getByLabelText("Backend"), { target: { value: "10230" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    // The project IS saved; an unrecorded block is how the NEXT project gets handed this one.
    await waitFor(() => expect(screen.getByText(warning)).toBeInTheDocument());
  });

  it("immutable fields are shown disabled, not hidden", () => {
    renderSection();
    expect(screen.getByLabelText("Krátky názov")).toBeDisabled();
    expect(screen.getByLabelText("Druh projektu")).toBeDisabled();
    expect(screen.getByLabelText("Prihlásenie")).toBeDisabled();
  });

  it("a user who may not operate the project cannot start editing", () => {
    renderSection({ canEdit: false });
    expect(screen.getByRole("button", { name: /Upraviť/ })).toBeDisabled();
  });
});
