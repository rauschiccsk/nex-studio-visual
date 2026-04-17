/**
 * Convenience barrel + streaming-specific types for the Architect
 * feature.
 *
 * Re-exports the core session and message types from their canonical
 * modules and adds SSE / NDJSON streaming types consumed by
 * ``sendMessageStream`` in ``services/api/architect.ts``.
 */

// Re-export canonical types so callers can ``import type { ... } from "@/types/architect"``
export type {
  ArchitectSessionStatus,
  ArchitectSessionCreate,
  ArchitectSessionUpdate,
  ArchitectSessionRead,
} from "./architectSession";

export type {
  ArchitectMessageRole,
  ArchitectMessageCreate,
  ArchitectMessageUpdate,
  ArchitectMessageRead,
} from "./architectMessage";

/* ------------------------------------------------------------------ */
/*  SSE / NDJSON streaming types                                       */
/* ------------------------------------------------------------------ */

/** A partial content chunk emitted during Claude streaming. */
export interface ArchitectStreamChunk {
  type: "chunk";
  content: string;
}

/** Final event sent when the Claude stream completes. */
export interface ArchitectStreamDone {
  type: "done";
  content: string;
  tokens: {
    input_tokens: number | null;
    output_tokens: number | null;
  };
}

/** Error event emitted when the Claude stream encounters a failure. */
export interface ArchitectStreamError {
  type: "error";
  content: string;
}

/** Discriminated union of all SSE event payloads. */
export type ArchitectStreamEvent =
  | ArchitectStreamChunk
  | ArchitectStreamDone
  | ArchitectStreamError;
