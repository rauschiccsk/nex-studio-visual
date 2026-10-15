// Backlog — deferred future customer requirements (E2, CR-NS-041).
// Mirrors backend/schemas/backlog.py.

import type { WorkKind } from "@/lib/workKind";

export type BacklogPriority = "low" | "medium" | "high" | "critical";
export type BacklogStatus = "open" | "included" | "realized" | "rejected";

export interface BacklogItemRead {
  id: string;
  project_id: string;
  number: number; // display id: REQ-{number}
  title: string;
  description: string | null;
  priority: BacklogPriority;
  status: BacklogStatus;
  version_id: string | null;
  realized_at: string | null;
  /** DEV-121: why the requirement was rejected — written to its ticket in Plane as well. */
  rejection_reason: string | null;
  /** DEV-121: the requirement's ticket in Plane (DEV-n) and its address, once founded. */
  plane_identifier: string | null;
  plane_url: string | null;
  created_at: string;
  updated_at: string;
  /** DEV-121: the change is saved, but its ticket in Plane did not follow — said to the person. */
  plane_warning?: string | null;
}

export interface BacklogItemCreate {
  project_id: string;
  title: string;
  description?: string | null;
  priority?: BacklogPriority;
}

export interface BacklogItemUpdate {
  title?: string;
  description?: string | null;
  priority?: BacklogPriority;
  status?: BacklogStatus;
  rejection_reason?: string;
  version_id?: string;
}

export type TicketType = "Bug" | "Task";

/** What „Založiť tiket" offers to edit — or the ticket that exists (DEV-121). */
export interface BacklogTicketDraft {
  title: string;
  description: string;
  ticket_type: TicketType;
  responsible_options: string[];
  responsible: string | null;
  plane_identifier: string | null;
  plane_url: string | null;
}

export interface BacklogTicketCreate {
  title: string;
  description: string;
  work_kind: WorkKind;
  ticket_type: TicketType;
  responsible: string;
}
