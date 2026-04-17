/**
 * Unit tests for {@link MessageStream} and {@link StreamingIndicator}.
 *
 * Tests cover:
 *   1. StreamingIndicator renders animated dots and label
 *   2. MessageStream calls sendMessageStream when content is provided
 *   3. onChunk accumulates content and calls onStreamingChange
 *   4. onDone finalizes message and calls onMessageComplete
 *   5. onError surfaces error via callback
 *   6. Stream is aborted on unmount
 *   7. MessageStream renders StreamingIndicator while waiting
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import StreamingIndicator from "@/components/architect/StreamingIndicator";
import type { ArchitectStreamEvent } from "@/types/architect";
import type { ArchitectMessageRead } from "@/types/architectMessage";

/* ------------------------------------------------------------------ */
/*  Mock sendMessageStream                                             */
/* ------------------------------------------------------------------ */

let mockSendMessageStream: Mock;
const mockAbort = vi.fn();

vi.mock("@/services/api/architect", () => ({
  sendMessageStream: (...args: unknown[]) => mockSendMessageStream(...args),
}));

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

beforeEach(() => {
  vi.resetAllMocks();

  // Default: capture callbacks, return controller with mock abort
  mockSendMessageStream = vi.fn(
    (
      _sessionId: string,
      _content: string,
      _onChunk: (content: string) => void,
      _onDone: (event: ArchitectStreamEvent & { type: "done" }) => void,
      _onError?: (error: Error) => void,
    ) => {
      return { abort: mockAbort, signal: new AbortController().signal };
    },
  );
});

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function importMessageStream() {
  const mod = await import("@/components/architect/MessageStream");
  return mod.default;
}

/* ------------------------------------------------------------------ */
/*  StreamingIndicator Tests                                           */
/* ------------------------------------------------------------------ */

describe("StreamingIndicator", () => {
  it("renders with default label", () => {
    render(<StreamingIndicator />);

    const indicator = screen.getByTestId("streaming-indicator");
    expect(indicator).toBeInTheDocument();
    expect(indicator).toHaveTextContent("Thinking...");
  });

  it("renders with custom label", () => {
    render(<StreamingIndicator label="Generating" />);

    expect(screen.getByTestId("streaming-indicator")).toHaveTextContent(
      "Generating...",
    );
  });

  it("has accessible role=status", () => {
    render(<StreamingIndicator />);

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders three animated dots", () => {
    const { container } = render(<StreamingIndicator />);

    // Three dot spans inside the aria-hidden wrapper
    const dots = container.querySelectorAll(".animate-bounce");
    expect(dots.length).toBe(3);
  });
});

/* ------------------------------------------------------------------ */
/*  MessageStream Tests                                                */
/* ------------------------------------------------------------------ */

describe("MessageStream", () => {
  it("calls sendMessageStream when content is provided", async () => {
    const MessageStream = await importMessageStream();
    const onStreamingChange = vi.fn();
    const onMessageComplete = vi.fn();

    render(
      <MessageStream
        sessionId="sess-123"
        content="Hello"
        onStreamingChange={onStreamingChange}
        onMessageComplete={onMessageComplete}
      />,
    );

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalledTimes(1);
    });

    expect(mockSendMessageStream).toHaveBeenCalledWith(
      "sess-123",
      "Hello",
      expect.any(Function), // onChunk
      expect.any(Function), // onDone
      expect.any(Function), // onError
    );
  });

  it("does not call sendMessageStream when content is null", async () => {
    const MessageStream = await importMessageStream();

    render(
      <MessageStream
        sessionId="sess-123"
        content={null}
        onStreamingChange={vi.fn()}
        onMessageComplete={vi.fn()}
      />,
    );

    // Give effect time to run
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(mockSendMessageStream).not.toHaveBeenCalled();
  });

  it("accumulates chunks via onChunk callback", async () => {
    const onStreamingChange = vi.fn();
    const MessageStream = await importMessageStream();

    render(
      <MessageStream
        sessionId="sess-123"
        content="Hello"
        onStreamingChange={onStreamingChange}
        onMessageComplete={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalled();
    });

    // Extract the onChunk callback
    const onChunk = mockSendMessageStream.mock.calls[0]![2] as (
      c: string,
    ) => void;

    // Simulate chunks
    act(() => {
      onChunk("Hello");
    });

    expect(onStreamingChange).toHaveBeenCalledWith(true, "Hello");

    act(() => {
      onChunk(" world");
    });

    expect(onStreamingChange).toHaveBeenCalledWith(true, "Hello world");
  });

  it("finalizes message via onDone callback", async () => {
    const onMessageComplete = vi.fn();
    const onStreamingChange = vi.fn();
    const MessageStream = await importMessageStream();

    render(
      <MessageStream
        sessionId="sess-123"
        content="Hello"
        onStreamingChange={onStreamingChange}
        onMessageComplete={onMessageComplete}
      />,
    );

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalled();
    });

    const onDone = mockSendMessageStream.mock.calls[0]![3] as (
      event: ArchitectStreamEvent & { type: "done" },
    ) => void;

    act(() => {
      onDone({
        type: "done",
        content: "Full response text",
        tokens: { input_tokens: 10, output_tokens: 20 },
      });
    });

    expect(onMessageComplete).toHaveBeenCalledTimes(1);

    const msg: ArchitectMessageRead = onMessageComplete.mock.calls[0]![0];
    expect(msg.role).toBe("assistant");
    expect(msg.content).toBe("Full response text");
    expect(msg.input_tokens).toBe(10);
    expect(msg.output_tokens).toBe(20);
    expect(msg.session_id).toBe("sess-123");

    // Streaming should be reset
    expect(onStreamingChange).toHaveBeenCalledWith(false, "");
  });

  it("surfaces errors via onError callback", async () => {
    const onError = vi.fn();
    const MessageStream = await importMessageStream();

    render(
      <MessageStream
        sessionId="sess-123"
        content="Hello"
        onStreamingChange={vi.fn()}
        onMessageComplete={vi.fn()}
        onError={onError}
      />,
    );

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalled();
    });

    const streamOnError = mockSendMessageStream.mock.calls[0]![4] as (
      err: Error,
    ) => void;

    act(() => {
      streamOnError(new Error("Stream failed"));
    });

    expect(onError).toHaveBeenCalledWith(expect.any(Error));
    expect((onError.mock.calls[0]![0] as Error).message).toBe("Stream failed");
  });

  it("aborts stream on unmount", async () => {
    const MessageStream = await importMessageStream();

    const { unmount } = render(
      <MessageStream
        sessionId="sess-123"
        content="Hello"
        onStreamingChange={vi.fn()}
        onMessageComplete={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalled();
    });

    unmount();

    expect(mockAbort).toHaveBeenCalled();
  });

  it("renders StreamingIndicator while streaming with no content", async () => {
    const MessageStream = await importMessageStream();

    render(
      <MessageStream
        sessionId="sess-123"
        content="Hello"
        onStreamingChange={vi.fn()}
        onMessageComplete={vi.fn()}
      />,
    );

    // The component starts streaming immediately, should show indicator
    await waitFor(() => {
      expect(screen.getByTestId("streaming-indicator")).toBeInTheDocument();
    });
  });
});
