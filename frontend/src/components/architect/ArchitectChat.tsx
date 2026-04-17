import { useRef, useEffect, useState } from "react";
import type { ArchitectMessageRead } from "../../types/architectMessage";

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface ArchitectChatProps {
  /** Loaded conversation messages (oldest first). */
  messages: ArchitectMessageRead[];
  /** Called when the user submits a new message. */
  onSendMessage: (content: string) => void;
  /** True while streaming an assistant response. */
  isStreaming?: boolean;
  /** Partial content accumulated during streaming. */
  streamingContent?: string;
  /** Disables input (e.g. user lacks permission). */
  disabled?: boolean;
  /** Placeholder text shown when input is disabled. */
  disabledReason?: string;
  /** Error message to display above the input area. */
  error?: string | null;
  /** True while the message history is loading. */
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

/**
 * Reusable Architect chat component with message list, input textarea,
 * and auto-scroll.  Designed to be used inside {@link ArchitectPage}
 * for both project-level and module-level Architect sessions.
 *
 * Streaming integration (ReadableStream) is handled by the parent
 * via the `isStreaming` / `streamingContent` props — this component
 * only renders what it receives.
 */
export default function ArchitectChat({
  messages,
  onSendMessage,
  isStreaming = false,
  streamingContent = "",
  disabled = false,
  disabledReason,
  error,
  isLoading = false,
}: ArchitectChatProps) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /* Auto-scroll to bottom when messages change or streaming content updates */
  useEffect(() => {
    if (bottomRef.current?.scrollIntoView) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, streamingContent]);

  /* Auto-resize textarea */
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
    }
  }, [input]);

  const canSend = input.trim().length > 0 && !disabled && !isStreaming;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSend) return;
    onSendMessage(input.trim());
    setInput("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Ctrl+Enter or Cmd+Enter to send
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      if (canSend) {
        onSendMessage(input.trim());
        setInput("");
      }
    }
  }

  return (
    <div
      className="flex flex-col h-full"
      data-testid="architect-chat"
    >
      {/* ---- Message list ---- */}
      <div
        className="flex-1 overflow-y-auto space-y-4 p-4"
        data-testid="architect-messages"
      >
        {isLoading && (
          <p
            className="text-center text-sm text-gray-500 dark:text-gray-400"
            data-testid="architect-loading"
          >
            Loading messages...
          </p>
        )}

        {!isLoading && messages.length === 0 && !isStreaming && (
          <p
            className="text-center text-sm text-gray-400 dark:text-gray-500 mt-8"
            data-testid="architect-empty"
          >
            No messages yet. Start the conversation below.
          </p>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            role={msg.role}
            content={msg.content}
            createdAt={msg.created_at}
          />
        ))}

        {/* Streaming assistant response (not yet saved) */}
        {isStreaming && streamingContent && (
          <MessageBubble
            role="assistant"
            content={streamingContent}
            isStreaming
          />
        )}

        {isStreaming && !streamingContent && (
          <div
            className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400"
            data-testid="architect-thinking"
          >
            <span className="inline-block h-2 w-2 rounded-full bg-primary-500 animate-pulse" />
            Thinking...
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* ---- Error banner ---- */}
      {error && (
        <div
          className="mx-4 mb-2 rounded-md bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300"
          role="alert"
          data-testid="architect-error"
        >
          {error}
        </div>
      )}

      {/* ---- Input area ---- */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-gray-200 dark:border-gray-700 p-4"
        data-testid="architect-input-form"
      >
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled || isStreaming}
            placeholder={
              disabled
                ? disabledReason ?? "Input disabled"
                : "Type your message... (Ctrl+Enter to send)"
            }
            rows={1}
            className="flex-1 resize-none rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:opacity-50"
            data-testid="architect-input"
          />
          <button
            type="submit"
            disabled={!canSend}
            className="btn-primary whitespace-nowrap"
            data-testid="architect-send"
          >
            {isStreaming ? "Streaming..." : "Send"}
          </button>
        </div>
        {disabled && disabledReason && (
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {disabledReason}
          </p>
        )}
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  MessageBubble                                                      */
/* ------------------------------------------------------------------ */

interface MessageBubbleProps {
  role: "user" | "assistant";
  content: string;
  createdAt?: string;
  isStreaming?: boolean;
}

function MessageBubble({ role, content, createdAt, isStreaming }: MessageBubbleProps) {
  const isUser = role === "user";

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
      data-testid={`architect-message-${role}`}
    >
      <div
        className={`max-w-[80%] rounded-lg px-4 py-3 text-sm ${
          isUser
            ? "bg-primary-600 text-white dark:bg-primary-500"
            : "bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
        } ${isStreaming ? "animate-pulse" : ""}`}
      >
        {/* Render content with preserved whitespace and line breaks */}
        <div className="whitespace-pre-wrap break-words">{content}</div>

        {createdAt && (
          <time
            className={`block mt-1 text-xs ${
              isUser
                ? "text-primary-200 dark:text-primary-300"
                : "text-gray-400 dark:text-gray-500"
            }`}
            dateTime={createdAt}
          >
            {new Date(createdAt).toLocaleTimeString()}
          </time>
        )}
      </div>
    </div>
  );
}
