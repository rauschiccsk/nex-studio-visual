// DEV-120 — the „Plane tikety" tab: the tickets of a project's module in the DEV register.
import api from "../api";
import type { components } from "./pipeline.generated";

export type ProjectPlaneTickets = components["schemas"]["ProjectPlaneTicketsRead"];
export type PlaneTicket = components["schemas"]["PlaneTicketRead"];
export type VersionTicket = components["schemas"]["VersionTicketRead"];

/** Open tickets, or with ``closed`` the done and cancelled ones — newest change first. */
export function listProjectPlaneTicketsApi(slug: string, closed: boolean): Promise<ProjectPlaneTickets> {
  return api.get<ProjectPlaneTickets>(`/projects/${slug}/plane-tickets`, { params: { closed } });
}

/** DEV-455 — „Zaradiť do verzie": the version solves the ticket; a second click returns the same link. */
export function putTicketIntoVersionApi(slug: string, issueId: string, versionId: string): Promise<VersionTicket> {
  return api.post<VersionTicket>(`/projects/${slug}/plane-tickets/${issueId}/version`, { version_id: versionId });
}

/** DEV-455 — „Vyradiť z verzie": while the version has not carried the ticket to the Staviteľ yet. */
export function takeTicketOutOfVersionApi(slug: string, issueId: string, versionId: string): Promise<VersionTicket> {
  return api.delete<VersionTicket>(`/projects/${slug}/plane-tickets/${issueId}/version/${versionId}`);
}
