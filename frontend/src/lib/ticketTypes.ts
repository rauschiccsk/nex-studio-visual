// The „Typ“ of a ticket in Plane as a person picks it — Poradca (DEV-119) offers it; the backend's ``TicketType``.
import type { components } from "@/services/api/pipeline.generated";

export type TicketType = components["schemas"]["PoradcaTicketCreate"]["ticket_type"];

export const TICKET_TYPES: { value: TicketType; label: string }[] = [
  { value: "Task", label: "Úloha (Task)" },
  { value: "Bug", label: "Chyba (Bug)" },
];
