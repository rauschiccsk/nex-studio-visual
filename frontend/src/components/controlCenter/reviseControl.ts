// Which control of Riadiace centrum sends „Uprav“ (``uprav``) — DEV-297.
//
// The engine offers ``uprav`` at every settled state („ask + uprav are universally valid“ in
// ``determine_available_actions``), and the cockpit, the agent and Poradca all tell the Manažér to press „Uprav“.
// Until 03.11.2026 only two places sent it: ApproveBar's „Upraviť“ — drawn only with ``schvalit``, which the board
// drops while tasks remain and on every conversation build — and the bar of an error. At the stop after the task
// plan (DEV-214) neither was there: NEX Manager 1.5.0 got Poradca's instruction for the plan as a QUESTION, twice,
// and the agent may not change the plan on a question. Director: „ja som nič nevložil, je to automatizovaný proces,
// ja stlačil som len tlačidlo.“ One rule now, read by ApproveBar and by the page for the conversation box.

import type { PipelineBoard } from "@/services/api/pipeline";

type BoardLike = Pick<PipelineBoard, "state" | "available_actions"> | null | undefined;

/** ApproveBar draws „Upraviť“ with its own comment box — at a phase gate, except Vizuál, where the conversation box
 *  is the change-request channel and a second box read as „2 editory“ (Director 13.07.2026). */
export function approveBarCarriesRevise(board: BoardLike): boolean {
  const actions = board?.available_actions ?? [];
  return actions.includes("schvalit") && actions.includes("uprav") && board?.state?.current_stage !== "vizual";
}

/** The conversation box carries „Uprav“ next to „Poslať“: the board offers it and no other control sends it.
 *  Vizuál keeps the box as its change channel. (A block needs no rule here: its bar takes the text and the box
 *  collapses — `blockRecoveryOwnsInput`.) */
export function composerOffersRevise(board: BoardLike): boolean {
  const state = board?.state;
  if (!state || !(board?.available_actions ?? []).includes("uprav")) return false;
  if (state.current_stage === "vizual") return false;
  return !approveBarCarriesRevise(board);
}
