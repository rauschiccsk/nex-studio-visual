/**
 * Frontendy projektu (DEV-522) — the frontends the project HAS, as a fact; „Pridať webový frontend“ for the admin account
 * only, with a reason. 26.11.2026 orthodox-register: a box „Projekt s webom“ listed a web frontend with its port and
 * offered „Prepnúť na Web“; the Director read it as the project's setting. „Je neprípustné, aby typ projektu a ďaľšie
 * jeho parametre, ktoré určíme pri zakladaní projektu boli zmenené počas tvorby projektu.“
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import WebSwitchPanel from "@/components/project/WebSwitchPanel";
import { getWebSwitchApi, switchToWebApi, type WebSwitch } from "@/services/api/projects";
import { useAuthStore } from "@/store/authStore";

vi.mock("@/services/api/projects", () => ({ getWebSwitchApi: vi.fn(), switchToWebApi: vi.fn() }));

const APP: WebSwitch["surfaces"] = [
  { name: "app", label: "Aplikácia", directory: "frontend", service: "frontend", port: 10241, login: "username", address_suffix: "" },
];
const BOTH: WebSwitch["surfaces"] = [
  { name: "admin", label: "Administrácia", directory: "frontend", service: "frontend", port: 10241, login: "username", address_suffix: "-admin" },
  { name: "web", label: "Web", directory: "web", service: "web", port: 10245, login: "email", address_suffix: "" },
];
const REASON = "Pri založení mal byť projekt webový.";

function as(username: string) {
  useAuthStore.setState({ user: { id: "u", username, role: "ri" } as never });
}

function frontends(): string[] {
  const panel = screen.getByRole("region", { name: "Frontendy projektu" });
  return [...panel.querySelectorAll(":scope > ul > li")].map((li) => li.textContent ?? "");
}

beforeEach(() => {
  vi.mocked(getWebSwitchApi).mockReset();
  vi.mocked(switchToWebApi).mockReset();
});

describe("WebSwitchPanel — Frontendy projektu (DEV-522)", () => {
  it("anyone else sees only the frontends the project has — nothing to add, no web it does not have", async () => {
    as("dominik");
    vi.mocked(getWebSwitchApi).mockResolvedValue({
      allowed: false,
      refusal: "Webový frontend pridáva len účet admin — čo sa nastaví pri založení projektu, sa počas tvorby nemení.",
      web_port: null,
      surfaces: APP,
      after: [],
      warnings: [],
    });
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={vi.fn()} />);

    await screen.findByRole("region", { name: "Frontendy projektu" });
    await waitFor(() => expect(frontends()).toEqual(["Aplikácia — priečinok frontend/, prihlásenie menom a heslom, port 10241"]));
    expect(screen.queryByText(/web\//)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /web/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/sa pridať nedá/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Projekt s webom|Prepnúť na Web/)).not.toBeInTheDocument();
  });

  it("the offer is the admin's alone on the screen too — whatever the server answers", async () => {
    as("dominik");
    vi.mocked(getWebSwitchApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10245, surfaces: APP, after: BOTH, warnings: [] });
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={vi.fn()} />);

    await screen.findByRole("region", { name: "Frontendy projektu" });
    await waitFor(() => expect(frontends()).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Pridať webový frontend" })).not.toBeInTheDocument();
  });

  it("the admin adds the web frontend only with a reason, and sees what the project will have", async () => {
    as("admin");
    vi.mocked(getWebSwitchApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10245, surfaces: APP, after: BOTH, warnings: [] });
    vi.mocked(switchToWebApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10245, surfaces: BOTH, after: [], warnings: [] });
    const onSwitched = vi.fn();
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={onSwitched} />);

    const add = await screen.findByRole("button", { name: "Pridať webový frontend" });
    expect(frontends()).toEqual(["Aplikácia — priečinok frontend/, prihlásenie menom a heslom, port 10241"]);
    const block = screen.getByRole("heading", { name: "Pridať webový frontend" }).parentElement as HTMLElement;
    expect([...block.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "Administrácia — priečinok frontend/, prihlásenie menom a heslom, port 10241",
      "Web — priečinok web/, prihlásenie e-mailom a heslom, registrácia, port 10245",
    ]);
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Dôvod"), { target: { value: "   " } });
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Dôvod"), { target: { value: `  ${REASON} ` } });
    fireEvent.click(add);

    await waitFor(() => expect(onSwitched).toHaveBeenCalledTimes(1));
    expect(switchToWebApi).toHaveBeenCalledWith("p-1", REASON);
  });

  it("the admin reads why it cannot be added", async () => {
    as("admin");
    vi.mocked(getWebSwitchApi).mockResolvedValue({
      allowed: false,
      refusal: "Projekt už bol nasadený do PROD — jeho typ a frontendy sa po založení nemenia.",
      web_port: null,
      surfaces: APP,
      after: [],
      warnings: [],
    });
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={vi.fn()} />);

    expect(
      await screen.findByText(
        "Webový frontend sa pridať nedá: Projekt už bol nasadený do PROD — jeho typ a frontendy sa po založení nemenia.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pridať webový frontend" })).not.toBeInTheDocument();
  });

  it("a Web project names both frontends and offers nothing", async () => {
    as("admin");
    vi.mocked(getWebSwitchApi).mockResolvedValue({
      allowed: false,
      refusal: "Projekt už je Web — má administráciu aj web.",
      web_port: null,
      surfaces: BOTH,
      after: [],
      warnings: [],
    });
    render(<WebSwitchPanel projectId="p-1" isWeb onSwitched={vi.fn()} />);

    await screen.findByRole("region", { name: "Frontendy projektu" });
    await waitFor(() => expect(frontends()).toHaveLength(2));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/sa pridať nedá/)).not.toBeInTheDocument();
  });

  it("on the app template the admin reads that the web comes from it and the project moves to its newest release", async () => {
    as("admin");
    vi.mocked(getWebSwitchApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10245, surfaces: APP, after: BOTH, warnings: [] });
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onTemplate onSwitched={vi.fn()} />);

    expect(await screen.findByText(/Administrácia aj server projektu pritom prejdú na najnovšiu verziu šablóny/)).toBeInTheDocument();
  });

  it("without the template nothing is said about it", async () => {
    as("admin");
    vi.mocked(getWebSwitchApi).mockResolvedValue({ allowed: true, refusal: null, web_port: 10245, surfaces: APP, after: BOTH, warnings: [] });
    render(<WebSwitchPanel projectId="p-1" isWeb={false} onSwitched={vi.fn()} />);

    await screen.findByRole("button", { name: "Pridať webový frontend" });
    expect(screen.queryByText(/najnovšiu verziu šablóny/)).not.toBeInTheDocument();
  });
});
