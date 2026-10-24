/**
 * DEV-220 — every Decision Card offers the Manažér's own decision, whatever options the agent wrote.
 *
 * 24.10.2026 Author Asistent 0.1.0: none of the options on a card suited the Director, the card had a text field —
 * „Pokyn pre AI partnera (nepovinné)“ — but „Rozhodnúť → ďalšie“ stayed grey until he picked one of them. His own
 * answer showed only where the agent allowed it, and the agent allowed it by exception. Director: „na kartách
 * otázok nie je možnosť zadať vlastné rozhodnutie … mne nevyhovuje ani jeden. Túto možnosť treba doplniť do
 * kokpitu.“
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/pipeline", () => api);

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import type { PipelineBoard } from "@/services/api/pipeline";

const VERSION = "v220";
const QUESTION = "Ako uzavrieť prístup k úlohám asistenta?";
const OWN_TEXT = "Aplikácia má jedného používateľa, ktorý smie všetko — rozdelenie právomocí zruš.";

// The agent's card as Author Asistent wrote it: two options, nothing about an answer of his own.
const CARD = {
  id: "m1",
  version_id: VERSION,
  stage: "navrh",
  author: "ai_agent",
  recipient: "manazer",
  kind: "consultation",
  content: "Previerka Návrhu.",
  status: "sent",
  seq: 10,
  created_at: "2026-10-24T17:38:36Z",
  payload: {
    consultation: {
      id: "navrh-previerka-2",
      source: "auditor_upfront",
      decisions: [
        {
          key: "admin_ulohy",
          question: QUESTION,
          options: [
            { id: "a", label: "Každý vidí len svoje úlohy", recommended: true },
            { id: "b", label: "Administrátor vidí len počty úloh" },
          ],
        },
        {
          key: "dalsia",
          question: "Druhá otázka",
          options: [
            { id: "a", label: "Áno", recommended: true },
            { id: "b", label: "Nie" },
          ],
        },
      ],
    },
  },
};

function board(): PipelineBoard {
  return {
    available_actions: ["decide"],
    state: { status: "blocked", block_reason: "decision_needed", current_stage: "navrh" },
    recent_messages: [CARD],
  } as unknown as PipelineBoard;
}

function bar(onBoard = vi.fn()) {
  return render(
    <MemoryRouter>
      <DecisionCardsBar board={board()} versionId={VERSION} onBoard={onBoard} />
    </MemoryRouter>,
  );
}

const own = () => screen.getByRole("button", { name: /^Vlastné rozhodnutie/ });
const decide = () => screen.getByRole("button", { name: /Rozhodnúť/ });
const field = () => screen.getByRole("textbox") as HTMLTextAreaElement;

beforeEach(() => {
  api.postPipelineActionApi.mockReset();
  window.localStorage.clear();
});

describe("DEV-220 — the Manažér's own decision on every card", () => {
  it("a card whose agent offered no own answer still offers „Vlastné rozhodnutie“ under its options", () => {
    bar();

    const choices = screen.getAllByRole("button", { pressed: false }).map((b) => b.textContent ?? "");
    expect(choices.map((c) => c.split(/Odporúčané|Žiadna/)[0])).toEqual([
      "Každý vidí len svoje úlohy",
      "Administrátor vidí len počty úloh",
      "Vlastné rozhodnutie",
    ]);
  });

  it("chosen, the field below is the decision: required, and it unlocks „Rozhodnúť“ once it holds text", () => {
    bar();

    fireEvent.click(own());

    expect(own()).toHaveAttribute("aria-pressed", "true");
    expect(field()).toHaveAttribute("placeholder", "Tvoje rozhodnutie — napíš, ako to má byť");
    expect(decide()).toBeDisabled();
    fireEvent.change(field(), { target: { value: "   " } });
    expect(decide()).toBeDisabled();
    fireEvent.change(field(), { target: { value: OWN_TEXT } });
    expect(decide()).toBeEnabled();
  });

  it("text written first counts too — he chooses „Vlastné rozhodnutie“ after writing, as the Director did", () => {
    bar();

    fireEvent.change(field(), { target: { value: OWN_TEXT } });
    expect(decide()).toBeDisabled();
    fireEvent.click(own());

    expect(field()).toHaveValue(OWN_TEXT);
    expect(decide()).toBeEnabled();
  });

  it("sends the text as his decision — no option of the agent rides along", async () => {
    const onBoard = vi.fn();
    api.postPipelineActionApi.mockResolvedValue(board());
    bar(onBoard);

    fireEvent.click(own());
    fireEvent.change(field(), { target: { value: `  ${OWN_TEXT}  ` } });
    fireEvent.click(decide());
    await waitFor(() => expect(onBoard).toHaveBeenCalledTimes(1));

    expect(api.postPipelineActionApi).toHaveBeenCalledWith(VERSION, {
      action: "decide",
      payload: { decision_key: "admin_ulohy", free_text: OWN_TEXT },
    });
  });

  it("an option picked after it takes over — the text goes back to being the instruction beside the option", async () => {
    api.postPipelineActionApi.mockResolvedValue(board());
    bar();

    fireEvent.click(own());
    fireEvent.change(field(), { target: { value: OWN_TEXT } });
    fireEvent.click(screen.getByRole("button", { name: /Každý vidí len svoje úlohy/ }));

    expect(own()).toHaveAttribute("aria-pressed", "false");
    expect(field()).toHaveAttribute("placeholder", "Pokyn pre AI partnera (nepovinné) — napíš, čo má spraviť");
    fireEvent.click(decide());
    await waitFor(() => expect(api.postPipelineActionApi).toHaveBeenCalledTimes(1));
    expect(api.postPipelineActionApi).toHaveBeenCalledWith(VERSION, {
      action: "decide",
      payload: { decision_key: "admin_ulohy", option_id: "a", note: OWN_TEXT },
    });
  });

  it("the line under the card says it can be his own decision", () => {
    bar();

    expect(screen.getByText(/Vyber možnosť alebo napíš vlastné rozhodnutie a potvrď\./)).toBeInTheDocument();
  });
});
