// The „Typ“ of a ticket in Plane as a person picks it — the Zásobník (DEV-121) and Poradca (DEV-119) offer the same.
import type { TicketType } from "@/types/backlog";

export const TICKET_TYPES: { value: TicketType; label: string }[] = [
  { value: "Task", label: "Úloha (Task)" },
  { value: "Bug", label: "Chyba (Bug)" },
];
