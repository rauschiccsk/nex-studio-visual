/**
 * DEV-175 — a version is founded only over saved work, whichever way the Manažér founds it.
 *
 * The engine refuses (409) with what is unsaved; the screen that founded shows the New-Version page's Uložiť /
 * Zahodiť right where the Manažér is. 21.10.2026 Career Asistent 1.1.0 was founded from Dedo's brief over 13
 * unsaved changes — that button never asked.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PlaneTicketFastFix from "@/components/planeTickets/PlaneTicketFastFix";
import UnsavedWorkPanel from "@/components/riadiace/UnsavedWorkPanel";
import { unsavedWorkOf } from "@/components/riadiace/unsavedWork";
import { ApiError } from "@/services/api";
import { startFastFixApi } from "@/services/api/pipeline";
import type { PlaneTicket } from "@/services/api/planeTickets";
import { commitGitApi, discardGitApi, getGitStatusApi, type GitStatus } from "@/services/api/projects";

vi.mock("@/services/api/projects", () => ({
  commitGitApi: vi.fn(),
  discardGitApi: vi.fn(),
  getGitStatusApi: vi.fn(),
}));
vi.mock("@/services/api/pipeline", () => ({ startFastFixApi: vi.fn() }));
const openVersionCockpit = vi.fn();
vi.mock("@/hooks/useOpenVersionCockpit", () => ({ useOpenVersionCockpit: () => openVersionCockpit }));

const MESSAGE =
  "Projekt má neuložené zmeny (1). Ulož ich alebo zahoď — inak by kokpit nevedel, čo do novej verzie patrí. " +
  "Verzia sa nezaložila.";
const DIRTY: GitStatus = { clean: false, dirty_count: 1, files: [{ code: "M", path: "frontend/a.ts" }], truncated: false };
const TWO_DIRTY: GitStatus = { ...DIRTY, dirty_count: 2 };
const CLEAN: GitStatus = { clean: true, dirty_count: 0, files: [], truncated: false };

function refused(unsaved: GitStatus | null = DIRTY): ApiError {
  return new ApiError(409, "conflict", {
    detail: { message: MESSAGE, project_id: "p-1", ...(unsaved ? { unsaved_work: unsaved } : {}) },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(commitGitApi).mockResolvedValue({ ok: true });
  vi.mocked(discardGitApi).mockResolvedValue({ ok: true });
  vi.mocked(getGitStatusApi).mockResolvedValue(CLEAN);
});

describe("unsavedWorkOf — reads the engine's refusal, and nothing else", () => {
  it("takes the sentence, the project and what is unsaved", () => {
    expect(unsavedWorkOf(refused())).toEqual({ message: MESSAGE, projectId: "p-1", status: DIRTY });
  });

  it("without a list when the engine could not tell", () => {
    expect(unsavedWorkOf(refused(null))).toEqual({ message: MESSAGE, projectId: "p-1", status: null });
  });

  it("⚠️ is not the 409 of a changed proposal or of a Zadanie already on disk", () => {
    expect(unsavedWorkOf(new ApiError(409, "Dedo medzitým napísal novší návrh; tento už neplatí."))).toBeNull();
    expect(
      unsavedWorkOf(new ApiError(409, "conflict", { detail: { message: "Pre túto verziu už zadanie existuje.", existing: "x" } })),
    ).toBeNull();
    expect(unsavedWorkOf(new Error("boom"))).toBeNull();
  });
});

describe("UnsavedWorkPanel — the New-Version page's Uložiť / Zahodiť wherever founding was refused", () => {
  it("saves the work of the refused project and says the version can be founded now", async () => {
    render(<UnsavedWorkPanel refusal={{ message: MESSAGE, projectId: "p-1", status: DIRTY }} />);

    fireEvent.click(screen.getByRole("button", { name: "Uložiť ich" }));

    expect(await screen.findByText("Neuložené zmeny sú vyriešené — klikni znova a verzia sa založí.")).toBeInTheDocument();
    expect(commitGitApi).toHaveBeenCalledWith("p-1");
    expect(getGitStatusApi).toHaveBeenCalledWith("p-1");
    expect(discardGitApi).not.toHaveBeenCalled();
  });

  it("throws the work away only after the second, explicit click", async () => {
    render(<UnsavedWorkPanel refusal={{ message: MESSAGE, projectId: "p-1", status: DIRTY }} />);

    fireEvent.click(screen.getByRole("button", { name: "Zahodiť" }));
    expect(discardGitApi).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Naozaj zahodiť 1 zmena" }));

    expect(await screen.findByText(/Neuložené zmeny sú vyriešené/)).toBeInTheDocument();
    expect(discardGitApi).toHaveBeenCalledWith("p-1");
  });

  it("does not call a tree clean that is not — it shows what is left", async () => {
    vi.mocked(getGitStatusApi).mockResolvedValue(TWO_DIRTY);
    render(<UnsavedWorkPanel refusal={{ message: MESSAGE, projectId: "p-1", status: DIRTY }} />);

    fireEvent.click(screen.getByRole("button", { name: "Uložiť ich" }));

    expect(await screen.findByText("Projekt má stále neuložené zmeny.")).toBeInTheDocument();
    expect(screen.getByText("Projekt má 2 zmeny, ktoré neboli uložené")).toBeInTheDocument();
    expect(screen.queryByText(/Neuložené zmeny sú vyriešené/)).not.toBeInTheDocument();
  });

  it("a save that failed says so and leaves the choice", async () => {
    vi.mocked(commitGitApi).mockResolvedValue({ ok: false, error: "pre-commit hook failed" });
    render(<UnsavedWorkPanel refusal={{ message: MESSAGE, projectId: "p-1", status: DIRTY }} />);

    fireEvent.click(screen.getByRole("button", { name: "Uložiť ich" }));

    expect(await screen.findByText("Uloženie zmien zlyhalo — zmeny zostali neuložené.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uložiť ich" })).toBeEnabled();
  });

  it("when the engine could not tell, it says that — there is nothing to save or discard", () => {
    render(<UnsavedWorkPanel refusal={{ message: MESSAGE, projectId: "p-1", status: null }} />);

    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Uložiť ich" })).not.toBeInTheDocument();
  });
});

describe("a fast fix from a ticket (DEV-122) refused for unsaved work", () => {
  const ticket = {
    issue_id: "i-1",
    identifier: "DEV-1",
    title: "Preklep v menu",
    text: "Nastvenia → Nastavenia",
    work_kind: "fix",
  } as unknown as PlaneTicket;

  it("offers Uložiť / Zahodiť in place and opens no build", async () => {
    vi.mocked(startFastFixApi).mockRejectedValue(refused());
    render(<PlaneTicketFastFix ticket={ticket} project={{ id: "p-1", slug: "demo", name: "Demo" }} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /Spustiť rýchlu opravu/ }));

    expect(await screen.findByText("Projekt má 1 zmena, ktoré neboli uložené")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uložiť ich" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /Spustiť rýchlu opravu/ })).toBeEnabled());
    expect(openVersionCockpit).not.toHaveBeenCalled();
  });
});
