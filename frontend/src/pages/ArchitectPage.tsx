import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import ArchitectChat from "../components/architect/ArchitectChat";
import MessageStream from "../components/architect/MessageStream";
import {
  createSessionApi,
  listSessionsApi,
  listMessagesApi,
} from "../services/api/architect";
import type { ArchitectSessionRead } from "../types/architectSession";
import type { ArchitectMessageRead } from "../types/architectMessage";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

/** Max messages to load per request. */
const MESSAGE_PAGE_SIZE = 100;

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

/**
 * Architect chat page — used for both project-level and module-level
 * conversations (DESIGN.md § 3.1).
 *
 * Routes:
 *   - `/projects/:slug/architect`                → project-level
 *   - `/projects/:slug/modules/:code/architect`  → module-level
 *
 * Session lifecycle:
 *   - On mount, tries to find an existing *active* session for the
 *     project (+ module if scoped).  If none exists, a new session is
 *     created automatically so the user can start chatting immediately.
 *   - Message history is loaded once the session is resolved.
 *
 * Streaming integration is wired in Task 21.3.  For now the send
 * handler adds the user message optimistically and delegates to the
 * parent to hook up ReadableStream later.
 */
function ArchitectPage() {
  const { slug, code } = useParams<{ slug: string; code?: string }>();
  const user = useAuthStore((s) => s.user);

  /* ---- State ---- */
  const [session, setSession] = useState<ArchitectSessionRead | null>(null);
  const [messages, setMessages] = useState<ArchitectMessageRead[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [error, setError] = useState<string | null>(null);

  /** Content pending to be sent via stream — triggers MessageStream. */
  const [pendingContent, setPendingContent] = useState<string | null>(null);

  /** Counter to force new stream even if same content is sent twice. */
  const streamKeyRef = useRef(0);
  const [streamKey, setStreamKey] = useState(0);

  /** Append a fully-formed message (e.g. after stream completes). */
  const appendMessage = useCallback((msg: ArchitectMessageRead) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  /** Replace streaming state — used during stream consumption. */
  const updateStreaming = useCallback(
    (streaming: boolean, content?: string) => {
      setIsStreaming(streaming);
      if (content !== undefined) setStreamingContent(content);
    },
    [],
  );

  /* ---- Session bootstrap ---- */
  useEffect(() => {
    if (!slug || !user) return;

    let cancelled = false;

    async function bootstrap() {
      setIsLoading(true);
      setError(null);

      try {
        // 1. Try to find an existing active session
        const resp = await listSessionsApi(slug!, {
          status: "active",
          module_id: code,
          limit: 1,
        });

        let activeSession: ArchitectSessionRead;

        if (resp.items.length > 0) {
          activeSession = resp.items[0]!;
        } else {
          // 2. Create a new session
          activeSession = await createSessionApi(slug!, {
            project_id: slug!,
            module_id: code ?? null,
            created_by: user!.id,
          });
        }

        if (cancelled) return;
        setSession(activeSession);

        // 3. Load message history
        const msgResp = await listMessagesApi(activeSession.id, {
          limit: MESSAGE_PAGE_SIZE,
        });

        if (cancelled) return;
        setMessages(msgResp.items);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load session",
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [slug, code, user]);

  /* ---- Send message handler — wires ReadableStream via MessageStream ---- */
  const handleSendMessage = useCallback(
    (content: string) => {
      if (!session) return;

      // Optimistic user message
      const optimistic: ArchitectMessageRead = {
        id: `temp-${Date.now()}`,
        session_id: session.id,
        role: "user",
        content,
        input_tokens: null,
        output_tokens: null,
        cost_usd: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, optimistic]);
      setError(null);

      // Trigger the stream — bump key so even identical messages restart
      streamKeyRef.current += 1;
      setStreamKey(streamKeyRef.current);
      setPendingContent(content);
    },
    [session],
  );

  /* ---- Role check ---- */
  const isRi = user?.role === "ri";

  /* ---- Render ---- */
  return (
    <section className="flex flex-col h-[calc(100vh-4rem)]" data-testid="architect-page">
      {/* Session header */}
      <header
        className="flex items-center gap-3 border-b border-gray-200 dark:border-gray-700 px-4 py-3"
        data-testid="architect-header"
      >
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Architect — {slug ?? "(unknown)"}
        </h2>

        {code && (
          <span
            className="inline-flex items-center rounded-full bg-primary-100 dark:bg-primary-900 px-2.5 py-0.5 text-xs font-medium text-primary-700 dark:text-primary-300"
            data-testid="architect-module-badge"
          >
            {code}
          </span>
        )}

        {session && (
          <span className="ml-auto text-xs text-gray-400 dark:text-gray-500">
            Session: {session.id.slice(0, 8)}...
          </span>
        )}
      </header>

      {/* Stream controller — triggers fetch + ReadableStream */}
      {session && pendingContent && (
        <MessageStream
          key={streamKey}
          sessionId={session.id}
          content={pendingContent}
          onStreamingChange={updateStreaming}
          onMessageComplete={(msg) => {
            appendMessage(msg);
            setPendingContent(null);
          }}
          onError={(err) => {
            setError(err.message);
            setPendingContent(null);
          }}
        />
      )}

      {/* Chat */}
      <div className="flex-1 min-h-0">
        <ArchitectChat
          messages={messages}
          onSendMessage={handleSendMessage}
          isStreaming={isStreaming}
          streamingContent={streamingContent}
          disabled={!isRi}
          disabledReason={
            !isRi ? "Only users with the ri role can send messages." : undefined
          }
          error={error}
          isLoading={isLoading}
        />
      </div>
    </section>
  );
}

export default ArchitectPage;
