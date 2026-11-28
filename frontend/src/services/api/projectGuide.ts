// DEV-536 — Sprievodca of a project's app: whether it is on and the switch that turns it off for a special app. Apart
// from the projects client: the panel asks for it whatever the page mocks.
import type { components } from "@/services/api/pipeline.generated";
import api from "../api";

export type GuideSwitch = components["schemas"]["GuideSwitchRead"];

export function getGuideApi(projectId: string): Promise<GuideSwitch> {
  return api.get<GuideSwitch>(`/projects/${projectId}/guide`);
}

export function setGuideApi(projectId: string, on: boolean): Promise<GuideSwitch> {
  return api.put<GuideSwitch>(`/projects/${projectId}/guide`, { on });
}
