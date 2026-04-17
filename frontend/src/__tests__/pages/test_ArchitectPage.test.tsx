/**
 * Unit tests for {@link ArchitectPage} and {@link ArchitectChat}.
 *
 * Tests cover:
 *   1. Page renders header with project slug
 *   2. Module badge shown when module code is present
 *   3. Empty state when no messages
 *   4. Messages rendered in the list
 *   5. Send button disabled for non-ri roles
 *   6. Send button enabled for ri role
 *   7. User can type and submit a message
 *   8. Error banner displayed when error is set
 *   9. Loading state shown while fetching
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ArchitectSessionRead } from "@/types/architectSession";
import type { ArchitectMessageRead } from "@/types/architectMessage";
import type { PaginatedResponse } from "@/types/common";

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_SESSION: ArchitectSessionRead = {
  id: "sess-1111-2222-3333-444444444444",
  project_id: "my-project",
  module_id: null,
  status: "active",
  created_by: "user-1",
  closed_at: null,
  created_at: "2026-04-17T10:00:00Z",
  updated_at: "2026-04-17T10:00:00Z",
};

const MOCK_MESSAGES: ArchitectMessageRead[] = [
  {
    id: "msg-1",
    session_id: MOCK_SESSION.id,
    role: "user",
    content: "Hello architect",
    input_tokens: 10,
    output_tokens: null,
    cost_usd: null,
    created_at: "2026-04-17T10:01:00Z",
    updated_at: "2026-04-17T10:01:00Z",
  },
  {
    id: "msg-2",
    session_id: MOCK_SESSION.id,
    role: "assistant",
    content: "Hello! How can I help?",
    input_tokens: null,
    output_tokens: 15,
    cost_usd: "0.000100",
    created_at: "2026-04-17T10:01:05Z",
    updated_at: "2026-04-17T10:01:05Z",
  },
];

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

// react-router-dom
let routeParams: Record<string, string | undefined> = { slug: "my-project" };

vi.mock("react-router-dom", () => ({
  useParams: () => routeParams,
}));

// authStore
let mockUser: { id: string; username: string; email: string; role: string; is_active: boolean; created_at: string; updated_at: string } | null = {
  id: "user-1",
  username: "zoltan",
  email: "z@test.com",
  role: "ri",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ user: mockUser }),
}));

// Architect API
const listSessionsMock: Mock = vi.fn();
const createSessionMock: Mock = vi.fn();
const listMessagesMock: Mock = vi.fn();
const sendMessageStreamMock: Mock = vi.fn(() => ({
  abort: vi.fn(),
  signal: new AbortController().signal,
}));

vi.mock("@/services/api/architect", () => ({
  listSessionsApi: (...args: unknown[]) => listSessionsMock(...args),
  createSessionApi: (...args: unknown[]) => createSessionMock(...args),
  listMessagesApi: (...args: unknown[]) => listMessagesMock(...args),
  sendMessageStream: (...args: unknown[]) => sendMessageStreamMock(...args),
}));

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

beforeEach(() => {
  vi.resetAllMocks();
  routeParams = { slug: "my-project" };
  mockUser = {
    id: "user-1",
    username: "zoltan",
    email: "z@test.com",
    role: "ri",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };

  // Default: existing session with messages
  listSessionsMock.mockResolvedValue({
    items: [MOCK_SESSION],
    total: 1,
    skip: 0,
    limit: 1,
  } satisfies PaginatedResponse<ArchitectSessionRead>);

  listMessagesMock.mockResolvedValue({
    items: MOCK_MESSAGES,
    total: 2,
    skip: 0,
    limit: 100,
  } satisfies PaginatedResponse<ArchitectMessageRead>);
});

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function importPage() {
  const mod = await import("@/pages/ArchitectPage");
  return mod.default;
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("ArchitectPage", () => {
  it("renders header with project slug", async () => {
    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-header")).toBeInTheDocument();
    });

    expect(screen.getByText(/Architect — my-project/)).toBeInTheDocument();
  });

  it("shows module badge when code param is present", async () => {
    routeParams = { slug: "my-project", code: "AUTH" };
    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-module-badge")).toBeInTheDocument();
    });

    expect(screen.getByTestId("architect-module-badge")).toHaveTextContent(
      "AUTH",
    );
  });

  it("does not show module badge for project-level", async () => {
    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-header")).toBeInTheDocument();
    });

    expect(
      screen.queryByTestId("architect-module-badge"),
    ).not.toBeInTheDocument();
  });

  it("shows loading state initially", async () => {
    // Make session list hang
    listSessionsMock.mockReturnValue(new Promise(() => {}));

    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-loading")).toBeInTheDocument();
    });
  });

  it("renders messages after loading", async () => {
    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getAllByTestId("architect-message-user").length).toBe(1);
    });

    expect(screen.getByText("Hello architect")).toBeInTheDocument();
    expect(screen.getByText("Hello! How can I help?")).toBeInTheDocument();
    expect(
      screen.getAllByTestId("architect-message-assistant").length,
    ).toBe(1);
  });

  it("shows empty state when no messages", async () => {
    listMessagesMock.mockResolvedValue({
      items: [],
      total: 0,
      skip: 0,
      limit: 100,
    });

    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-empty")).toBeInTheDocument();
    });

    expect(
      screen.getByText("No messages yet. Start the conversation below."),
    ).toBeInTheDocument();
  });

  it("creates a new session when none exists", async () => {
    listSessionsMock.mockResolvedValue({
      items: [],
      total: 0,
      skip: 0,
      limit: 1,
    });
    createSessionMock.mockResolvedValue(MOCK_SESSION);

    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(createSessionMock).toHaveBeenCalledWith("my-project", {
        project_id: "my-project",
        module_id: null,
        created_by: "user-1",
      });
    });
  });

  it("disables send button for non-ri role", async () => {
    mockUser = {
      ...mockUser!,
      role: "ha",
    };

    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-send")).toBeDisabled();
    });
  });

  it("enables send button for ri role with input", async () => {
    const user = userEvent.setup();
    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-input")).toBeInTheDocument();
    });

    // Initially disabled (empty input)
    expect(screen.getByTestId("architect-send")).toBeDisabled();

    // Type something
    await user.type(screen.getByTestId("architect-input"), "Test message");

    expect(screen.getByTestId("architect-send")).toBeEnabled();
  });

  it("submits a message and shows optimistic user bubble", async () => {
    const user = userEvent.setup();
    const Page = await importPage();
    render(<Page />);

    // Wait for messages to load
    await waitFor(() => {
      expect(screen.getByText("Hello architect")).toBeInTheDocument();
    });

    await user.type(screen.getByTestId("architect-input"), "New question");
    await user.click(screen.getByTestId("architect-send"));

    await waitFor(() => {
      expect(screen.getByText("New question")).toBeInTheDocument();
    });

    // Input should be cleared
    expect(screen.getByTestId("architect-input")).toHaveValue("");
  });

  it("shows error banner when session load fails", async () => {
    listSessionsMock.mockRejectedValue(new Error("Network error"));

    const Page = await importPage();
    render(<Page />);

    await waitFor(() => {
      expect(screen.getByTestId("architect-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("architect-error")).toHaveTextContent(
      "Network error",
    );
  });
});
