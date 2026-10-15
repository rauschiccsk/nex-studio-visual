// DEV-120 — the „Plane tikety" tab: the tickets of a project's module in the DEV register.
import api from "../api";
import type { components } from "./pipeline.generated";
import type { BacklogItemRead } from "@/types/backlog";

export type ProjectPlaneTickets = components["schemas"]["ProjectPlaneTicketsRead"];
export type PlaneTicket = components["schemas"]["PlaneTicketRead"];

/** Open tickets, or with ``closed`` the done and cancelled ones — newest change first. */
export function listProjectPlaneTicketsApi(slug: string, closed: boolean): Promise<ProjectPlaneTickets> {
  return api.get<ProjectPlaneTickets>(`/projects/${slug}/plane-tickets`, { params: { closed } });
}

/** DEV-122 — „Do Zásobníka": the ticket becomes a linked requirement, once; a second click returns the same one. */
export function planeTicketToBacklogApi(slug: string, issueId: string): Promise<BacklogItemRead> {
  return api.post<BacklogItemRead>(`/projects/${slug}/plane-tickets/${issueId}/backlog`);
}
