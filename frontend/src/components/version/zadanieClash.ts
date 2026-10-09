// DEV-40 — reading a Zadanie conflict off the engine's 409, and joining two texts for „Doplniť".

import { ApiError } from "@/services/api";

/** What is on disk instead of the text the editor started from — the engine sends it with the 409 (ICCINT-71). */
export interface ZadanieClash {
  existing: string;
  message: string;
}

export function zadanieClashOf(err: unknown): ZadanieClash | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const detail = (err.data as { detail?: { existing?: unknown; message?: unknown } } | null)?.detail;
  if (typeof detail?.existing !== "string") return null;
  return {
    existing: detail.existing,
    message:
      typeof detail.message === "string"
        ? detail.message
        : "Pre túto verziu už zadanie existuje. Neprepísal som ho — pozri, čo v ňom je, a rozhodni sa.",
  };
}

/** „Doplniť": his text after what is on disk, one blank line between. */
export function appendZadanie(existing: string, mine: string): string {
  return `${existing.trimEnd()}\n\n${mine.trim()}`;
}
