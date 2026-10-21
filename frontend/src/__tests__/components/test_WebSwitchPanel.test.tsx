/**
 * WebSwitchPanel — DEV-164: a Štandardný project becomes a Web project with one deliberate click, or says why not.
 *
 * Director 20.10.2026: „Ideme kokpit naučiť stavať projekt s dvoma frontendmi.“ The panel shows what the switch gives
 * (the two frontends, each with its folder, sign-in and port) before it is done, asks once more, and a Web project
 * names its frontends instead of offering the switch.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import WebSwitchPanel from "@/components/project/WebSwitchPanel";
import { getWebSwitchApi, switchToWebApi, type WebSwitch } from "@/services/api/projects";

vi.mock("@/services/api/projects", () => ({ getWebSwitchApi: vi.fn(), switchToWebApi: vi.fn() }));

const SURFACES: WebSwitch["surfaces"] = [
  { name: "admin", label: "Administrácia", directory: "frontend", service: "frontend", port: 10251, login: "username", address_suffix: "-admin" },
  { name: "web", label: "Web", directory: "web", service: "web", port: 10255, login: "email", address_suffix: "" },
];

beforeEach(() => {
  vi.mocked(getWebSwitchApi).mockReset();
  vi.mocked(switchToWebApi).mockReset();
});

describe("WebSwitchPanel (DEV-164)", () => {
  it("shows what the switch gives and switches only after the second click", async () => {
    vi.mocked(getWebSwitchApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10255, surfaces: SURFACES, warnings: [] });
    vi.mocked(switchToWebApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10255, surfaces: SURFACES, warnings: [] });
    const onSwitched = vi.fn();
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={onSwitched} />);

    expect(await screen.findByText(/prihlásenie e-mailom a heslom, registrácia/)).toBeInTheDocument();
    expect(screen.getByText(/port 10255/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Prepnúť na Web" }));
    expect(switchToWebApi).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Áno, prepnúť na Web" }));

    await waitFor(() => expect(onSwitched).toHaveBeenCalledOnce());
    expect(switchToWebApi).toHaveBeenCalledWith("p-1");
  });

  it("a project that cannot be switched says why and offers no button", async () => {
    vi.mocked(getWebSwitchApi).mockResolvedValue({
      allowed: false,
      refusal: "Projekt sa prihlasuje tokenom.",
      web_port: null,
      surfaces: SURFACES.slice(0, 1),
      warnings: [],
    });
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={vi.fn()} />);

    expect(await screen.findByText(/Na Web sa tento projekt prepnúť nedá: Projekt sa prihlasuje tokenom\./)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("a Web project names its two frontends and offers no switch", async () => {
    vi.mocked(getWebSwitchApi).mockResolvedValue({
      allowed: false,
      refusal: "Projekt už je Web — má administráciu aj web.",
      web_port: null,
      surfaces: SURFACES,
      warnings: [],
    });
    render(<WebSwitchPanel projectId="p-1" isWeb onSwitched={vi.fn()} />);

    expect(await screen.findByText("Administrácia")).toBeInTheDocument();
    expect(screen.getByText("Web")).toBeInTheDocument();
    expect(screen.queryByText(/prepnúť nedá/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
