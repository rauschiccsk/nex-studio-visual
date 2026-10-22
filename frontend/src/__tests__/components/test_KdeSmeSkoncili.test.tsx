/**
 * DEV-151 — „Kde sme skončili“: a person writes why the development stands and what next; the newest note is at the
 * top of Riadiace centrum and on the Prehľad, older ones stay, and „Pozastaviť“ offers the field without holding up
 * the pause.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import KdeSmeSkoncili from "@/components/riadiace/KdeSmeSkoncili";
import PlanUlohRail from "@/components/riadiace/PlanUlohRail";
import { noteLine } from "@/lib/versionNote";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { getTaskPlan } from "@/services/api/versions";
import * as notesApi from "@/services/api/versionNotes";
import { formatDateTime } from "@/utils/format";

vi.mock("@/services/api/versionNotes", () => ({ listVersionNotesApi: vi.fn(), addVersionNoteApi: vi.fn() }));
vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const AT = new Date(2026, 9, 22, 19, 15).toISOString();
const note = (text: string, id = text): notesApi.VersionNote => ({
  id,
  version_id: "v1",
  text,
  author_name: "Zoltán Rausch",
  created_at: AT,
});

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("Kde sme skončili in Riadiace centrum", () => {
  it("shows the newest note with its author and time, the older ones a click away", async () => {
    vi.mocked(notesApi.listVersionNotesApi).mockResolvedValue([note("Čakáme na Tibora."), note("Staršia poznámka.")]);
    render(<KdeSmeSkoncili versionId="v1" />);

    expect(await screen.findByText(`„Čakáme na Tibora.“ — Zoltán Rausch, ${formatDateTime(AT)}`)).toBeInTheDocument();
    expect(screen.queryByText(/Staršia poznámka/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Staršie poznámky (1)" }));
    expect(screen.getByText(/„Staršia poznámka\.“/)).toBeInTheDocument();
  });

  it("a written note is saved and shown at the top", async () => {
    vi.mocked(notesApi.listVersionNotesApi).mockResolvedValue([]);
    vi.mocked(notesApi.addVersionNoteApi).mockResolvedValue(note("Pokračovať úlohou 1.1.4."));
    render(<KdeSmeSkoncili versionId="v1" />);

    expect(await screen.findByText("zatiaľ nikto nenapísal.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Napísať poznámku" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Kde sme skončili" }), {
      target: { value: "Pokračovať úlohou 1.1.4." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Uložiť poznámku" }));

    expect(await screen.findByText(/„Pokračovať úlohou 1\.1\.4\.“ — Zoltán Rausch/)).toBeInTheDocument();
    expect(notesApi.addVersionNoteApi).toHaveBeenCalledWith("v1", "Pokračovať úlohou 1.1.4.");
  });

  it("a note that failed to save says so and keeps the text", async () => {
    vi.mocked(notesApi.listVersionNotesApi).mockResolvedValue([]);
    vi.mocked(notesApi.addVersionNoteApi).mockRejectedValue(new Error("boom"));
    render(<KdeSmeSkoncili versionId="v1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Napísať poznámku" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Kde sme skončili" }), { target: { value: "Nestratiť." } });
    fireEvent.click(screen.getByRole("button", { name: "Uložiť poznámku" }));

    expect(await screen.findByText(/Poznámku sa nepodarilo uložiť/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Kde sme skončili" })).toHaveValue("Nestratiť.");
  });

  it("a failed load is not „nobody wrote“", async () => {
    vi.mocked(notesApi.listVersionNotesApi).mockRejectedValue(new Error("boom"));
    render(<KdeSmeSkoncili versionId="v1" />);

    expect(await screen.findByText(/sa nepodarilo načítať/)).toBeInTheDocument();
    expect(screen.queryByText("zatiaľ nikto nenapísal.")).not.toBeInTheDocument();
  });

  it("one line names an author who is gone", () => {
    expect(noteLine({ text: "x", author_name: null, created_at: AT })).toBe(`„x“ — bývalý používateľ, ${formatDateTime(AT)}`);
  });
});

const RUNNING = {
  available_actions: ["pause"],
  state: { status: "agent_working", current_stage: "programovanie", pause_reason: null, next_action: "" },
  recent_messages: [],
} as unknown as PipelineBoard;

function renderRail() {
  vi.mocked(getTaskPlan).mockResolvedValue({ plan: [], cross_cutting: [] } as never);
  render(<PlanUlohRail versionId="v1" messages={[]} board={RUNNING} onBoard={() => {}} />, { wrapper: MemoryRouter });
}

describe("Pozastaviť offers „Kde sme skončili“", () => {
  it("the note is saved, then the build is paused", async () => {
    vi.mocked(notesApi.addVersionNoteApi).mockResolvedValue(note("Ideme na novú zákazku."));
    vi.mocked(postPipelineActionApi).mockResolvedValue(RUNNING);
    renderRail();

    fireEvent.change(await screen.findByRole("textbox", { name: "Prečo pozastavuješ a čo ďalej" }), {
      target: { value: "Ideme na novú zákazku." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pozastaviť" }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "pause" }));
    expect(notesApi.addVersionNoteApi).toHaveBeenCalledWith("v1", "Ideme na novú zákazku.");
  });

  it("without a note it only pauses; a note that fails does not hold the pause up", async () => {
    vi.mocked(postPipelineActionApi).mockResolvedValue(RUNNING);
    renderRail();

    fireEvent.click(await screen.findByRole("button", { name: "Pozastaviť" }));
    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledTimes(1));
    expect(notesApi.addVersionNoteApi).not.toHaveBeenCalled();

    vi.mocked(notesApi.addVersionNoteApi).mockRejectedValue(new Error("boom"));
    fireEvent.change(screen.getByRole("textbox", { name: "Prečo pozastavuješ a čo ďalej" }), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Pozastaviť" }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/stavbu pozastavujem aj tak/)).toBeInTheDocument();
  });
});
