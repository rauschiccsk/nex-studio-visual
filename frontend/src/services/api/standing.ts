// DEV-150 — „Kde stojíme“ on the Prehľad: where every project's development stands.
import api from "../api";
import type { components } from "./pipeline.generated";

export type Standing = components["schemas"]["StandingRead"];
export type ProjectStanding = components["schemas"]["ProjectStanding"];
export type OpenVersionStanding = components["schemas"]["OpenVersionStanding"];

/** The projects the person works on — those waiting on a person first, the longest waiting at the top. */
export function getStandingApi(): Promise<Standing> {
  return api.get<Standing>("/standing");
}
