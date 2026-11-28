/**
 * Sprievodca v aplikácii (DEV-536) — on by default, switched off only for a special app; the app's manifest keeps it.
 * The Director 28.11.2026: „Má byť default, že tam je ale v prípade, že ide o nejakú špeciálnu aplikáciu aby sme to
 * mohli vypnúť.“
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import GuideSwitchPanel from "@/components/project/GuideSwitchPanel";
import { getGuideApi, setGuideApi, type GuideSwitch } from "@/services/api/projectGuide";

vi.mock("@/services/api/projectGuide", () => ({ getGuideApi: vi.fn(), setGuideApi: vi.fn() }));

const ON: GuideSwitch = { applies: true, on: true, refusal: null, kit_version: "0.24.1", needed: "0.24.0", warnings: [] };
const OFF: GuideSwitch = { ...ON, on: false };

beforeEach(() => {
  vi.mocked(getGuideApi).mockReset();
  vi.mocked(setGuideApi).mockReset();
});

async function panel() {
  return screen.findByRole("region", { name: "Sprievodca v aplikácii" });
}

describe("GuideSwitchPanel — Sprievodca v aplikácii (DEV-536)", () => {
  it("says it is on and switches it off for a special app", async () => {
    vi.mocked(getGuideApi).mockResolvedValue(ON);
    vi.mocked(setGuideApi).mockResolvedValue(OFF);
    render(<GuideSwitchPanel projectId="p-1" canEdit />);

    expect(await panel()).toHaveTextContent("Zapnutý. Aplikácia má „Ako urobiť“");
    fireEvent.click(screen.getByRole("button", { name: "Vypnúť Sprievodcu" }));

    await waitFor(() => expect(setGuideApi).toHaveBeenCalledWith("p-1", false));
    expect(await screen.findByText(/Aplikácia má „Ako začať“ s písanou príručkou/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zapnúť Sprievodcu" })).toBeInTheDocument();
  });

  it("switches it on again", async () => {
    vi.mocked(getGuideApi).mockResolvedValue(OFF);
    vi.mocked(setGuideApi).mockResolvedValue(ON);
    render(<GuideSwitchPanel projectId="p-1" canEdit />);

    fireEvent.click(await screen.findByRole("button", { name: "Zapnúť Sprievodcu" }));

    await waitFor(() => expect(setGuideApi).toHaveBeenCalledWith("p-1", true));
    expect(await screen.findByRole("button", { name: "Vypnúť Sprievodcu" })).toBeInTheDocument();
  });

  it("someone who may not change the project reads it and has no button", async () => {
    vi.mocked(getGuideApi).mockResolvedValue(ON);
    render(<GuideSwitchPanel projectId="p-1" canEdit={false} />);

    expect(await panel()).toHaveTextContent("Zapnutý.");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("an older template says why — and offers no switch", async () => {
    const refusal = "Sprievodcu pozná šablóna od verzie nex-app-kit 0.24.0, projekt stojí na 0.23.0. Najprv povýš šablónu aplikácie.";
    vi.mocked(getGuideApi).mockResolvedValue({ ...OFF, kit_version: "0.23.0", refusal });
    render(<GuideSwitchPanel projectId="p-1" canEdit />);

    expect(await panel()).toHaveTextContent(refusal);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/Vypnutý\./)).toBeNull();
  });

  it("a project without the template shows nothing", async () => {
    vi.mocked(getGuideApi).mockResolvedValue({ ...OFF, applies: false });
    const { container } = render(<GuideSwitchPanel projectId="p-1" canEdit />);

    await waitFor(() => expect(getGuideApi).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("a switch that could not be done says so, and what was there stays", async () => {
    vi.mocked(getGuideApi).mockResolvedValue(ON);
    vi.mocked(setGuideApi).mockRejectedValue(new Error("network"));
    render(<GuideSwitchPanel projectId="p-1" canEdit />);

    fireEvent.click(await screen.findByRole("button", { name: "Vypnúť Sprievodcu" }));

    expect(await screen.findByText(/Sprievodcu sa nepodarilo vypnúť/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vypnúť Sprievodcu" })).toBeInTheDocument();
  });

  it("what finished only partly is said", async () => {
    vi.mocked(getGuideApi).mockResolvedValue(ON);
    vi.mocked(setGuideApi).mockResolvedValue({ ...OFF, warnings: ["Zmena je uložená v gite projektu, ale na GitHub sa neodoslala."] });
    render(<GuideSwitchPanel projectId="p-1" canEdit />);

    fireEvent.click(await screen.findByRole("button", { name: "Vypnúť Sprievodcu" }));

    expect(await screen.findByText("Zmena je uložená v gite projektu, ale na GitHub sa neodoslala.")).toBeInTheDocument();
  });
});
