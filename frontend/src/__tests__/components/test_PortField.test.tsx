/**
 * Tests for the PortField component.
 *
 * Covers:
 *   - Renders label, input, and suggest button
 *   - Range validation (1–65535) shown on blur
 *   - Async availability check on blur (available → no error)
 *   - Async availability check on blur (conflict → error message)
 *   - "Suggest Next" button fetches and sets suggested port
 *   - "Suggest Next" error handling
 *   - Empty value does not trigger validation
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ---------------------------------------------------------------------------
// Mocks — must be declared before component import
// ---------------------------------------------------------------------------

const mockCheckPortAvailability = vi.fn<(port: number) => Promise<boolean>>();
const mockSuggestNextAvailablePort = vi.fn<
  (type: "backend" | "frontend" | "db") => Promise<number>
>();

vi.mock("@/services/api/port-registry", () => ({
  checkPortAvailability: (...args: unknown[]) =>
    mockCheckPortAvailability(...(args as [number])),
  suggestNextAvailablePort: (...args: unknown[]) =>
    mockSuggestNextAvailablePort(...(args as ["backend" | "frontend" | "db"])),
}));

// ---------------------------------------------------------------------------
// Dynamic import (after mocks are installed)
// ---------------------------------------------------------------------------

async function importPortField() {
  const mod = await import("@/components/projects/PortField");
  return mod.default;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultProps = {
  label: "Backend Port",
  type: "backend" as const,
  value: "",
  onChange: vi.fn(),
  placeholder: "9176",
  testId: "backend-port",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PortField", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders label, input, and suggest button", async () => {
    const PortField = await importPortField();
    render(<PortField {...defaultProps} />);

    expect(screen.getByText("Backend Port")).toBeInTheDocument();
    expect(screen.getByTestId("backend-port-input")).toBeInTheDocument();
    expect(screen.getByTestId("backend-port-suggest")).toBeInTheDocument();
    expect(screen.getByText("Suggest Next")).toBeInTheDocument();
  });

  it("shows range error for port > 65535 on blur", async () => {
    const PortField = await importPortField();
    render(<PortField {...defaultProps} value="70000" />);

    fireEvent.blur(screen.getByTestId("backend-port-input"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Port must be 1–65535");
    });

    // Should NOT call the API for out-of-range ports
    expect(mockCheckPortAvailability).not.toHaveBeenCalled();
  });

  it("shows range error for port < 1 on blur", async () => {
    const PortField = await importPortField();
    render(<PortField {...defaultProps} value="0" />);

    fireEvent.blur(screen.getByTestId("backend-port-input"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Port must be 1–65535");
    });
  });

  it("shows no error when port is available", async () => {
    mockCheckPortAvailability.mockResolvedValueOnce(true);

    const PortField = await importPortField();
    render(<PortField {...defaultProps} value="9200" />);

    fireEvent.blur(screen.getByTestId("backend-port-input"));

    await waitFor(() => {
      expect(mockCheckPortAvailability).toHaveBeenCalledWith(9200);
    });

    // No error should be displayed
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows conflict error when port is in use", async () => {
    mockCheckPortAvailability.mockResolvedValueOnce(false);

    const PortField = await importPortField();
    render(<PortField {...defaultProps} value="9176" />);

    fireEvent.blur(screen.getByTestId("backend-port-input"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Port 9176 is already in use",
      );
    });
  });

  it("does not validate when value is empty", async () => {
    const PortField = await importPortField();
    render(<PortField {...defaultProps} value="" />);

    fireEvent.blur(screen.getByTestId("backend-port-input"));

    // Give time for potential async calls
    await waitFor(() => {
      expect(mockCheckPortAvailability).not.toHaveBeenCalled();
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("Suggest Next button fetches and sets suggested port", async () => {
    const onChangeMock = vi.fn();
    mockSuggestNextAvailablePort.mockResolvedValueOnce(9300);

    const PortField = await importPortField();
    const user = userEvent.setup();
    render(<PortField {...defaultProps} onChange={onChangeMock} />);

    await user.click(screen.getByTestId("backend-port-suggest"));

    await waitFor(() => {
      expect(mockSuggestNextAvailablePort).toHaveBeenCalledWith("backend");
      expect(onChangeMock).toHaveBeenCalledWith("9300");
    });
  });

  it("Suggest Next uses correct type for frontend port", async () => {
    const onChangeMock = vi.fn();
    mockSuggestNextAvailablePort.mockResolvedValueOnce(9400);

    const PortField = await importPortField();
    const user = userEvent.setup();
    render(
      <PortField
        {...defaultProps}
        type="frontend"
        label="Frontend Port"
        testId="frontend-port"
        onChange={onChangeMock}
      />,
    );

    await user.click(screen.getByTestId("frontend-port-suggest"));

    await waitFor(() => {
      expect(mockSuggestNextAvailablePort).toHaveBeenCalledWith("frontend");
      expect(onChangeMock).toHaveBeenCalledWith("9400");
    });
  });

  it("shows error when suggestion fails", async () => {
    mockSuggestNextAvailablePort.mockRejectedValueOnce(new Error("Network error"));

    const PortField = await importPortField();
    const user = userEvent.setup();
    render(<PortField {...defaultProps} />);

    await user.click(screen.getByTestId("backend-port-suggest"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Failed to get suggestion",
      );
    });
  });

  it("disables input and button when disabled prop is true", async () => {
    const PortField = await importPortField();
    render(<PortField {...defaultProps} disabled />);

    expect(screen.getByTestId("backend-port-input")).toBeDisabled();
    expect(screen.getByTestId("backend-port-suggest")).toBeDisabled();
  });

  it("clears error on input change", async () => {
    mockCheckPortAvailability.mockResolvedValueOnce(false);

    const onChangeMock = vi.fn();
    const PortField = await importPortField();
    const { rerender } = render(
      <PortField {...defaultProps} value="9176" onChange={onChangeMock} />,
    );

    // Trigger conflict error
    fireEvent.blur(screen.getByTestId("backend-port-input"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    // Simulate typing — the parent updates value, onChange clears error
    fireEvent.change(screen.getByTestId("backend-port-input"), {
      target: { value: "9200" },
    });

    // Rerender with new value (simulating controlled component)
    rerender(
      <PortField {...defaultProps} value="9200" onChange={onChangeMock} />,
    );

    // Error should be cleared after change
    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });
});
