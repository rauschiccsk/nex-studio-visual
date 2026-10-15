// DEV-128 — what an empty Plán úloh says depends on when this build makes its plan.
//
// Director 15.10.2026: „Nevidím plán úloh.“ NEX Manager 1.3.0 stood in the Vizuál with no plan — how a phase build
// works: Návrh writes the design, and the first Programovanie turn builds the plan from it and from the Vizuál
// changes (orchestrator, „the FIRST build dispatch builds the EPIC→FEAT→TASK plan“). The rail said the plan would
// appear once the specification is agreed, which there was long done.

import type { PipelineState } from "@/services/api/pipeline";

export function emptyPlanSentence(state: Pick<PipelineState, "flow_type" | "mode"> | null | undefined): string {
  if (state?.flow_type === "fast_fix") return "Úloha rýchlej opravy vznikne, keď sa oprava začne stavať.";
  if (state && state.mode !== "conversation") {
    return "Plán úloh vznikne na začiatku Programovania — zo schváleného Návrhu a zmien vo Vizuáli.";
  }
  return "Plán sa objaví, keď sa dohodneme na špecifikácii.";
}
