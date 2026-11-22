// DEV-91, DEV-492 — a change that does not work in UAT becomes a ticket of the project the Manažér files himself.
//
// It used to become the text of a fast fix that started from it. With one way for every version (Director 22.11.2026:
// „Treba mať jednotný postup a to najprv verzia a potom priradzovanie tiketov.“) the text is filed as a ticket and put
// into a version or a fast fix on the „Plane tikety“ tab. Nothing starts by itself; the Manažér picks the work kind.
import type { PreparedText } from "@/components/planeTickets/PreparedTicketDialog";

export interface BrokenChange {
  version: string;
  customer: string;
  title: string;
  steps: string[];
  expected: string[];
  note: string;
}

/** The ticket's name and text, from the guide's change and what the Manažér saw instead. */
export function uatTicket(c: BrokenChange): PreparedText {
  const lines = [`V UAT u zákazníka ${c.customer} vo verzii ${c.version} nefunguje: ${c.title}.`];
  if (c.steps.length) lines.push("Kroky:", ...c.steps.map((s, i) => `${i + 1}. ${s}`));
  for (const e of c.expected) lines.push(`Očakávané: ${e}`);
  if (c.note.trim()) lines.push(`Čo sa stalo: ${c.note.trim()}`);
  lines.push("Oprav to tak, aby sa stalo očakávané.");
  return {
    title: `Nefunguje v UAT: ${c.title}`.slice(0, 255),
    description: lines.join("\n"),
    source: "uat",
    workKind: null,
  };
}
