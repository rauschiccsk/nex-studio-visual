/**
 * MessageStream — manages the ReadableStream connection for the
 * Architect chat.
 *
 * Responsibilities:
 *   - Calls {@link sendMessageStream} with onChunk / onDone / onError
 *     callbacks.
 *   - Accumulates streamed content in local state and surfaces it to
 *     the parent via the `onStreamingChange` callback so the chat UI
 *     can render the partial response.
 *   - On completion (`onDone`), builds a finalized
 *     {@link ArchitectMessageRead} and calls `onMessageComplete`.
 *   - Provides an `abort` handle so the parent can cancel the stream.
 *
 * Usage (from ArchitectPage):
 *
 * ```tsx
 * <MessageStream
 *   sessionId={session.id}
 *   content={pendingContent}
 *   onStreamingChange={(streaming, content) => { ... }}
 *   onMessageComplete={(msg) => appendMessage(msg)}
 *   onError={(err) => setError(err.message)}
 * />
 * ```
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { sendMessageStream } from "../../services/api/architect";
import StreamingIndicator from "./StreamingIndicator";
import type { ArchitectStreamEvent } from "../../types/architect";
import type { ArchitectMessageRead } from "../../types/architectMessage";

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface MessageStreamProps {
  /** The active session UUID. */
  sessionId: string;
  /** User message content to send.  A new stream is started whenever
   *  this value changes to a non-empty string. Pass `null` when idle. */
  content: string | null;
  /** Called whenever streaming state or accumulated content changes. */
  onStreamingChange: (isStreaming: boolean, content: string) => void;
  /** Called once the stream completes with the finalized assistant message. */
  onMessageComplete: (message: ArchitectMessageRead) => void;
  /** Called on stream or network errors. */
  onError?: (error: Error) => void;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function MessageStream({
  sessionId,
  content,
  onStreamingChange,
  onMessageComplete,
  onError,
}: MessageStreamProps) {
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamedContent, setStreamedContent] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  // Keep latest callbacks in refs to avoid re-triggering the effect
  const onStreamingChangeRef = useRef(onStreamingChange);
  onStreamingChangeRef.current = onStreamingChange;
  const onMessageCompleteRef = useRef(onMessageComplete);
  onMessageCompleteRef.current = onMessageComplete;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  /** Cancel any in-flight stream. */
  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  // Expose abort on unmount
  useEffect(() => {
    return () => {
      abort();
    };
  }, [abort]);

  /* ---- Start stream when content changes ---- */
  useEffect(() => {
    if (!content || !sessionId) return;

    // Cancel previous stream if any
    abort();

    let accumulated = "";
    setIsStreaming(true);
    setStreamedContent("");
    onStreamingChangeRef.current(true, "");

    const controller = sendMessageStream(
      sessionId,
      content,
      // onChunk
      (chunk: string) => {
        accumulated += chunk;
        setStreamedContent(accumulated);
        onStreamingChangeRef.current(true, accumulated);
      },
      // onDone
      (event: ArchitectStreamEvent & { type: "done" }) => {
        setIsStreaming(false);
        setStreamedContent("");
        onStreamingChangeRef.current(false, "");

        const finalMessage: ArchitectMessageRead = {
          id: `stream-${Date.now()}`,
          session_id: sessionId,
          role: "assistant",
          content: event.content,
          input_tokens: event.tokens.input_tokens,
          output_tokens: event.tokens.output_tokens,
          cost_usd: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };

        onMessageCompleteRef.current(finalMessage);
        abortRef.current = null;
      },
      // onError
      (error: Error) => {
        setIsStreaming(false);
        setStreamedContent("");
        onStreamingChangeRef.current(false, "");
        onErrorRef.current?.(error);
        abortRef.current = null;
      },
    );

    abortRef.current = controller;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, sessionId]);

  /* ---- Render streaming indicator when active but no content yet ---- */
  if (isStreaming && !streamedContent) {
    return <StreamingIndicator label="Thinking" />;
  }

  return null;
}
