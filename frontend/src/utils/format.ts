/**
 * Shared formatting utilities used across multiple pages.
 *
 * DEV-153 — every date on the cockpit's screen has one shape, the one NEX Inbox has (DEV-148): „20.10.2026“ and
 * „20.10.2026 08:30“ — no spaces, no seconds, day and month two-digit; where a short form stands without the year,
 * „16.10. 21:22“. The Director 19.10.2026 at NEX Inbox: „Chcel by som zbytočné medzery odstrániť“; for the cockpit
 * 20.10.2026: „Súhlasím“. The browser's ``sk-SK`` wrote „20. 10. 2026 8:30:15“. Only this file shapes a date — a test
 * stops a screen that would shape one itself. The time is the viewer's local time, as before.
 */

const two = (n: number): string => String(n).padStart(2, "0");

function parsed(value: string | Date | null | undefined): Date | null {
  if (value == null || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const day = (d: Date): string => `${two(d.getDate())}.${two(d.getMonth() + 1)}.`;
const clock = (d: Date): string => `${two(d.getHours())}:${two(d.getMinutes())}`;

/** „20.10.2026“ — „—“ when there is no date. */
export function formatDate(value: string | Date | null | undefined): string {
  const d = parsed(value);
  return d ? `${day(d)}${d.getFullYear()}` : "—";
}

/** „20.10.2026 08:30“ — „—“ when there is no date. */
export function formatDateTime(value: string | Date | null | undefined): string {
  const d = parsed(value);
  return d ? `${day(d)}${d.getFullYear()} ${clock(d)}` : "—";
}

/** „16.10. 21:22“ — without the year, where the screen keeps it short; „—“ when there is no date. */
export function formatShortDateTime(value: string | Date | null | undefined): string {
  const d = parsed(value);
  return d ? `${day(d)} ${clock(d)}` : "—";
}

/** „08:30“ — „—“ when there is no date. */
export function formatTime(value: string | Date | null | undefined): string {
  const d = parsed(value);
  return d ? clock(d) : "—";
}
