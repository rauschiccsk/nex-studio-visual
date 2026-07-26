/**
 * DeployBlockNotice — WHY the "Nasadiť" button is closed, said out loud (v4.0.54).
 *
 * "Overená verzia" is recomputed against live git on every read, so a finished version drops out of the
 * deployable list the moment the project's code moves on. Until now the UAT/PROD screen greyed "Nasadiť" out
 * in SILENCE — a Junior hit exactly that and had to be unblocked from a terminal (incident 2026-07-26). The
 * notice closes both halves of the dead end, and these tests pin them:
 *
 *   LEGIBILITY    — a plain-Slovak cause naming the affected version, with NO engineering jargon in it
 *                   (the last test is the guard: no "commit" / "HEAD" / "SHA" / "drift" reaches the screen).
 *   ACTIONABILITY — honest-by-construction (mirrors ReverifyNoFixBar / SchvalitBar): "Over znova" is drawn
 *                   ONLY when the backend says this user may post `overit_znovu` right now (`can_reverify`,
 *                   true solely for the `drift` cause — every other shape is rejected by that handler). A
 *                   button that 400s or 403s would just move the dead end one click further.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployBlockNotice from "@/components/deploy/DeployBlockNotice";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";
import type { DeployBlock, DeployBlockCause } from "@/types/deploy";

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/services/api/pipeline", () => ({
  postPipelineActionApi: vi.fn(),
}));

// The board the action returns — the notice ignores it (it reports upwards via `onReverifyStarted` so the
// page reloads the matrix), so its only job here is to make the mocked promise resolve.
const NEXT_BOARD = { state: { current_stage: "verifikacia" } } as unknown as PipelineBoard;

/** A backend `deployability` block; the defaults are the implicated-version shape (drift / stale_signoff). */
function block(cause: DeployBlockCause, over: Partial<DeployBlock> = {}): DeployBlock {
  return { cause, version_number: "1.2.0", version_id: "ver-42", can_reverify: false, ...over };
}

function notice(b: DeployBlock | null, onReverifyStarted: () => void = vi.fn()) {
  return <DeployBlockNotice block={b} projectSlug="demo" onReverifyStarted={onReverifyStarted} />;
}

const REVERIFY = /Over znova/;

describe("DeployBlockNotice — why Nasadiť is closed, and the way out", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(postPipelineActionApi).mockResolvedValue(NEXT_BOARD);
    navigateMock.mockReset();
  });

  it("renders NOTHING when a version is deployable (cause 'ok')", () => {
    const { container } = render(notice(block("ok", { version_number: null, version_id: null })));
    expect(container).toBeEmptyDOMElement();
  });

  it("explains the pause and offers 'Over znova' when this user may re-run it (drift + can_reverify)", () => {
    render(notice(block("drift", { can_reverify: true })));
    expect(screen.getByText(/Nasadenie je pozastavené/)).toBeInTheDocument();
    expect(screen.getByText(/Verziu 1\.2\.0 sme označili ako hotovú/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: REVERIFY })).toBeInTheDocument();
  });

  it("click on 'Over znova' posts `overit_znovu` for the implicated version, then tells the page to reload", async () => {
    const onReverifyStarted = vi.fn();
    render(notice(block("drift", { version_id: "ver-42", can_reverify: true }), onReverifyStarted));

    fireEvent.click(screen.getByRole("button", { name: REVERIFY }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("ver-42", { action: "overit_znovu" }));
    await waitFor(() => expect(onReverifyStarted).toHaveBeenCalled());
  });

  it("keeps the explanation but draws NO button when this user may NOT re-run it (drift, can_reverify false)", () => {
    const { container } = render(notice(block("drift", { can_reverify: false })));
    // The manager still learns WHY the button is closed…
    expect(screen.getByText(/Verziu 1\.2\.0 sme označili ako hotovú/)).toBeInTheDocument();
    // …and who can unblock it — but never a button whose request the backend would reject for them.
    expect(screen.getByText(/vlastník projektu alebo Manažér/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REVERIFY })).not.toBeInTheDocument();
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("routes a stale sign-off to the version instead of offering 'Over znova' (that handler rejects the shape)", () => {
    render(notice(block("stale_signoff")));
    expect(screen.getByText(/nanovo prekontrolovať a označiť ako hotovú/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REVERIFY })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Otvoriť verziu/ }));

    expect(navigateMock).toHaveBeenCalledWith("/projects/demo/versions/ver-42");
    expect(postPipelineActionApi).not.toHaveBeenCalled();
  });

  it("reports a running re-verification with NO action button (and shows the version bare, without the 'v')", () => {
    const { container } = render(notice(block("reverify_running", { version_number: "v1.2.0" })));
    expect(screen.getByText(/Overujem verziu 1\.2\.0/)).toBeInTheDocument();
    expect(screen.getByText(/Aplikácia sa práve spúšťa a kontroluje/)).toBeInTheDocument();
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("sends the manager to the project when no version was ever finished (cause 'none_finished')", () => {
    render(notice(block("none_finished", { version_number: null, version_id: null })));
    expect(screen.getByText(/Zatiaľ nie je čo nasadiť/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REVERIFY })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Otvoriť projekt/ }));

    expect(navigateMock).toHaveBeenCalledWith("/projects/demo");
  });

  it("surfaces a plain-Slovak error and does NOT report a start when the re-verify request fails", async () => {
    vi.mocked(postPipelineActionApi).mockRejectedValueOnce(new Error("boom"));
    const onReverifyStarted = vi.fn();
    render(notice(block("drift", { can_reverify: true }), onReverifyStarted));

    fireEvent.click(screen.getByRole("button", { name: REVERIFY }));

    await waitFor(() => expect(screen.getByText(/Opätovné overenie sa nepodarilo spustiť/)).toBeInTheDocument());
    expect(onReverifyStarted).not.toHaveBeenCalled();
  });

  // ── The wording guard: this notice exists BECAUSE a non-technical manager was left stranded by it ────────
  //
  // Every cause is a git fact underneath (the version's sign-off no longer matches the project's current
  // commit). None of that vocabulary may reach the screen — if it does, the notice fails at the very job it
  // was added for, however accurate it is.

  const JARGON = ["commit", "HEAD", "SHA", "drift"];

  it.each([
    ["drift", true],
    ["drift", false],
    ["reverify_running", false],
    ["version_busy", false],
    ["awaiting_signoff", false],
    ["stale_signoff", false],
    ["none_finished", false],
  ] as [DeployBlockCause, boolean][])(
    "states the '%s' cause (can_reverify=%s) without engineering jargon — a non-technical manager reads this",
    (cause, canReverify) => {
      const { container } = render(notice(block(cause, { can_reverify: canReverify })));
      const text = (container.textContent ?? "").toLowerCase();
      expect(text.length).toBeGreaterThan(0);
      for (const word of JARGON) {
        expect(text).not.toContain(word.toLowerCase());
      }
    },
  );

  it("calls a passed-but-unapproved version one click away, never 'stale' (awaiting_signoff)", () => {
    render(notice(block("awaiting_signoff")));

    // The ordinary post-check state. Saying "later work outranked the sign-off" here would be false on the
    // most common not-deployable state — the remedy is an approval, not a re-check.
    expect(screen.getByText(/chýba už len tvoje schválenie/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Over znova/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Otvoriť verziu/i })).toBeInTheDocument();
  });

  it("makes no self-unlock promise while the version is merely busy (version_busy)", () => {
    render(notice(block("version_busy")));

    // Nothing in this state re-anchors the version, so it must NOT claim the screen will unlock itself.
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/práve pracuje/i);
    expect(text).not.toMatch(/odomkne sa samo|odomkne samo/i);
    expect(screen.queryByRole("button", { name: /Over znova/i })).not.toBeInTheDocument();
  });

  it("renders NOTHING for a cause this bundle does not know (newer backend)", () => {
    const { container } = render(notice(block("kto_vie_co" as DeployBlockCause)));

    // Silence is the OLD behaviour; a confidently wrong claim would be worse than the bug this fixes.
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING when the payload has no deployability block at all (version skew)", () => {
    const { container } = render(notice(null));

    expect(container).toBeEmptyDOMElement();
  });
});
