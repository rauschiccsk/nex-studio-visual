// DEV-373 — the name of a build button comes from the cockpit, never from the component that draws it.
//
// 13.11.2026 Poradca told the Manažér to press „Schváliť“ while this screen drew „Schváliť na Hotovo“: the bars wrote
// their own names and Poradca kept a copy, and nine of them had drifted apart. The cockpit now works the names out
// for the build's state (backend/services/action_labels.py) and hands them out on the board; Poradca reads the same.
// The plain names (actionLabels.generated.json, written by `npm run codegen`) stand in only where no board is at
// hand — a page that draws the button before it has one.

import plainNames from "@/services/api/actionLabels.generated.json";
import type { PipelineBoard } from "@/services/api/pipeline";

export type ButtonAction = keyof typeof plainNames;

export function actionLabel(
  board: Pick<PipelineBoard, "action_labels"> | null | undefined,
  action: ButtonAction,
): string {
  return board?.action_labels?.[action] ?? plainNames[action];
}
