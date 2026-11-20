/**
 * DEV-447 — the strip says the preview is being prepared until it answers, and offers the link only then.
 *
 * Director 20.11.2026, Orthodox Hermenia 0.2.0: „Spustil som náhľad znova, na obrazovke nevidím nič čo by svedčilo, že
 * kokpit pracuje ale keď som spustil vizuál tak vypisuje, že sa na tom robí toto je tiež BUG“. The container ran within
 * seconds; Vite then prepared its libraries for a minute or two, and the strip offered the link the whole time.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import ApproveBar from "@/components/controlCenter/ApproveBar";
import LivePreviewBar from "@/components/controlCenter/LivePreviewBar";
import type { PipelineBoard, VizualPreview } from "@/services/api/pipeline";

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useNavigate: () => vi.fn(),
}));

const BOARD = {
  state: { current_stage: "vizual", status: "awaiting_manazer" },
  vizual_url: "https://vizual-orthodox-hermenia.isnex.eu",
  available_actions: ["schvalit", "restart_preview"],
} as unknown as PipelineBoard;
const STARTING: VizualPreview = { needed: true, state: "starting", url: null, reason: null };

describe("LivePreviewBar — a preview still preparing (DEV-447)", () => {
  it("says it is being prepared, shows work in progress and offers no link — not even the board's old address", () => {
    render(<LivePreviewBar board={BOARD} preview={STARTING} onRestart={vi.fn()} />);

    expect(screen.getByText("Náhľad sa pripravuje… — odkaz sa ukáže, keď bude pripravený.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("after an unusually long wait it says so and offers to start it again", () => {
    const onRestart = vi.fn();
    render(<LivePreviewBar board={BOARD} preview={STARTING} onRestart={onRestart} startingTooLong />);

    expect(screen.getByText("Náhľad sa pripravuje dlhšie než zvyčajne — ak sa neotvorí, spusti ho znova.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(onRestart).toHaveBeenCalledTimes(1);
  });

  it("once it answers, the link is there", () => {
    const running: VizualPreview = { ...STARTING, state: "running", url: "https://vizual-orthodox-hermenia.isnex.eu" };
    render(<LivePreviewBar board={BOARD} preview={running} onRestart={vi.fn()} />);

    expect(screen.getByRole("link", { name: "Otvoriť živý náhľad" })).toHaveAttribute(
      "href",
      "https://vizual-orthodox-hermenia.isnex.eu",
    );
  });

  it("the vizuál is not approved while its preview prepares — the waiting is said, not „spusti ho“", () => {
    render(<ApproveBar board={BOARD} versionId="v1" onBoard={vi.fn()} previewStarting />);

    expect(
      screen.getByText("Živý náhľad sa ešte pripravuje — počkaj, kým sa otvorí, pozri si ho a potom vizuál schváľ."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Živý náhľad nebeží/)).toBeNull();
    for (const button of screen.getAllByRole("button").filter((b) => /schváliť/i.test(b.textContent ?? ""))) {
      expect(button).toBeDisabled();
    }
    expect(screen.getAllByRole("button").some((b) => /schváliť/i.test(b.textContent ?? ""))).toBe(true);
  });
});
