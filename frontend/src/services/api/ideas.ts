// Nápady (DEV-318, docs/specs/ideas.md) — a new idea talked over with Poradca before any project exists. Backend:
// `backend/api/routes/poradca.py` (`/poradca/ideas`). The talk itself goes through Poradca's conversation routes.

import api from "../api";
import type { Idea, IdeaDetail, IdeaOutcome } from "../../types/poradca";
import type { PoradcaImageUpload } from "./poradca";

export function listIdeasApi(): Promise<Idea[]> {
  return api.get<Idea[]>("/poradca/ideas");
}

/** A new idea; ``title`` names it when the screen means a name of its own (DEV-350: a follow-up idea). */
export function createIdeaApi(
  question: string,
  attachments: PoradcaImageUpload[] = [],
  title?: string,
): Promise<IdeaDetail> {
  return api.post<IdeaDetail>(
    "/poradca/ideas",
    title ? { question, attachments, title } : { question, attachments },
  );
}

export function getIdeaApi(id: string): Promise<IdeaDetail> {
  return api.get<IdeaDetail>(`/poradca/ideas/${id}`);
}

export function throwAwayIdeaApi(id: string): Promise<void> {
  return api.delete<void>(`/poradca/ideas/${id}`);
}

/** The person founded `projectId` from the brief of `messageId` — its 0.1.0 gets the brief, the project its ticket. */
export function ideaFoundedApi(
  id: string,
  messageId: string,
  projectId: string,
): Promise<IdeaOutcome> {
  return api.post<IdeaOutcome>(`/poradca/ideas/${id}/founded`, {
    message_id: messageId,
    project_id: projectId,
  });
}

/** DEV-360 — the ticket of the project founded from the idea, when founding it failed; a second click returns it. */
export function ideaTicketApi(id: string): Promise<IdeaOutcome> {
  return api.post<IdeaOutcome>(`/poradca/ideas/${id}/ticket`);
}

/** The brief of `messageId` goes to the Zásobník of the existing project it names. */
export function ideaAddedApi(
  id: string,
  messageId: string,
): Promise<IdeaOutcome> {
  return api.post<IdeaOutcome>(`/poradca/ideas/${id}/added`, {
    message_id: messageId,
  });
}
