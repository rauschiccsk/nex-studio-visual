/**
 * ReturnToPripravaBar (DEV-367) — a version in Návrh or Vizuál goes back to Príprava.
 *
 * NEX Finance 0.1.0 reached Vizuál and the whole design was wrong; nothing on the screen took it back. Director
 * 12.11.2026: „Súhlasím, vrátime sa do prípravy, pretože celý projekt je zlý.“
 *
 * ⚠️ What these guards do not let change:
 *  - the bar is drawn only while the engine offers the action (a button that would do nothing never shows);
 *  - the first click sends nothing — it says what happens (the Návrh, the task plan and the Vizuál are thrown away,
 *    they stay in git's history, the Špecifikácia opens again) and asks again; only the second sends the action;
 *  - „Nevracať“ sends nothing; a failure is shown and the bar stays usable.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ReturnToPripravaBar from "@/components/controlCenter/ReturnToPripravaBar";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

function board(actions: string[]): PipelineBoard {
  return { state: null, recent_messages: [], available_actions: actions } as unknown as PipelineBoard;
}

const OFFERED = board(["ask", "uprav", "schvalit", "vratit_do_pripravy"]);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Vrátiť do Prípravy", () => {
  it("⚠️ without the engine's offer nothing is drawn", () => {
    const { container } = render(
      <ReturnToPripravaBar board={board(["ask", "uprav", "schvalit"])} versionId="v-1" onBoard={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("with the offer there is the button", () => {
    render(<ReturnToPripravaBar board={OFFERED} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByRole("button", { name: /^Vrátiť do Prípravy$/ })).toBeInTheDocument();
  });

  it("⚠️ the first click sends nothing — it says what happens and asks again; the second sends the action", async () => {
    const next = board([]);
    vi.mocked(postPipelineActionApi).mockResolvedValue(next);
    const onBoard = vi.fn();
    render(<ReturnToPripravaBar board={OFFERED} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /^Vrátiť do Prípravy$/ }));

    expect(postPipelineActionApi).not.toHaveBeenCalled();
    expect(screen.getByText(/Návrh, plán úloh a Vizuál sa zahodia/)).toBeInTheDocument();
    expect(screen.getByText(/ostanú v histórii gitu/)).toBeInTheDocument();
    expect(screen.getByText(/Špecifikácia sa znova otvorí/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Naozaj vrátiť do Prípravy/ }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "vratit_do_pripravy" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(next));
  });

  it("„Nevracať“ sends nothing and the bar is back as it was", () => {
    render(<ReturnToPripravaBar board={OFFERED} versionId="v-1" onBoard={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /^Vrátiť do Prípravy$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Nevracať/ }));

    expect(postPipelineActionApi).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Naozaj vrátiť do Prípravy/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Vrátiť do Prípravy$/ })).toBeInTheDocument();
  });

  it("⚠️ a failure is shown — it does not vanish without a word", async () => {
    vi.mocked(postPipelineActionApi).mockRejectedValue(new Error("engine odmietol"));
    const onBoard = vi.fn();
    render(<ReturnToPripravaBar board={OFFERED} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /^Vrátiť do Prípravy$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Naozaj vrátiť do Prípravy/ }));

    expect(await screen.findByText(/Vrátenie do Prípravy zlyhalo/)).toBeInTheDocument();
    expect(onBoard).not.toHaveBeenCalled();
  });
});
