/**
 * Component tests for UserForm — single component for both create + edit
 * flows in Settings → Users (DRY: replaces ~160 lines of duplicate JSX
 * across two inline forms in SettingsPage.tsx).
 *
 * Verifies mode-specific behaviour:
 *   - mode="create": username editable, password required, no Active checkbox
 *   - mode="edit":   username disabled, password optional (empty=keep), Active checkbox
 *
 * Shared: identical layout + Tailwind, password min 5 validation, Cancel callback.
 *
 * CR-NS-079: UserForm moved to the shared SettingsKit (nex-shared) and became
 * role-agnostic + field-driven, so the tests now import it from `nex-shared`
 * and pass Studio's `roleOptions` (shu/ha/ri) + `fieldSchema` (all fields,
 * password min 5). The behavioural assertions are unchanged — this is the
 * regression proof that the extracted component behaves exactly as before.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { UserForm, type UserFieldSchema, type UserRead } from "nex-shared";

// Studio's UserForm config — exactly what SettingsPage passes the kit form.
const ROLE_OPTIONS = [
  { value: "shu", label: "shu — Junior" },
  { value: "ha", label: "ha — Medior" },
  { value: "ri", label: "ri — Director" },
];
const FIELD_SCHEMA: UserFieldSchema = {
  username: true,
  names: true,
  telegram: true,
  passwordMinLength: 5,
};

function mkUser(overrides: Partial<UserRead> = {}): UserRead {
  return {
    id: "user-1",
    username: "tibi",
    email: "tibi@icc.sk",
    role: "ha",
    is_active: true,
    first_name: "Tibor",
    last_name: "Rausch",
    created_at: "2026-05-13T00:00:00Z",
    updated_at: "2026-05-13T00:00:00Z",
    ...overrides,
  };
}

describe("UserForm — mode='create'", () => {
  it("renders all 6 input fields", () => {
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByLabelText(/^meno$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/priezvisko/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/používateľské meno/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/heslo/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/rola/i)).toBeInTheDocument();
  });

  it("does not show the Active checkbox in create mode", () => {
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.queryByLabelText(/aktívny/i)).not.toBeInTheDocument();
  });

  it("username input is enabled (editable) in create mode", () => {
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByLabelText(/používateľské meno/i)).not.toBeDisabled();
  });

  it("disables Create button while password < 5 chars", () => {
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText(/používateľské meno/i), { target: { value: "tibi" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "tibi@icc.sk" } });
    fireEvent.change(screen.getByLabelText(/heslo/i), { target: { value: "abc" } });
    expect(screen.getByRole("button", { name: /vytvoriť/i })).toBeDisabled();
  });

  it("enables Create button when all required fields valid", () => {
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText(/používateľské meno/i), { target: { value: "tibi" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "tibi@icc.sk" } });
    fireEvent.change(screen.getByLabelText(/heslo/i), { target: { value: "abcde" } });
    expect(screen.getByRole("button", { name: /vytvoriť/i })).toBeEnabled();
  });

  it("submits with is_active=true by default + collected form data", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={onSubmit} onCancel={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText(/^meno$/i), { target: { value: "Tibor" } });
    fireEvent.change(screen.getByLabelText(/priezvisko/i), { target: { value: "Rausch" } });
    fireEvent.change(screen.getByLabelText(/používateľské meno/i), { target: { value: "tibi" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "tibi@icc.sk" } });
    fireEvent.change(screen.getByLabelText(/heslo/i), { target: { value: "abcde" } });
    fireEvent.click(screen.getByRole("button", { name: /vytvoriť/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({
      username: "tibi",
      email: "tibi@icc.sk",
      password: "abcde",
      role: "shu",
      first_name: "Tibor",
      last_name: "Rausch",
      telegram_chat_id: "",
      is_active: true,
    });
  });

  it("Cancel calls onCancel", () => {
    const onCancel = vi.fn();
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="" onSubmit={vi.fn()} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /zrušiť/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("shows the error message when provided", () => {
    render(
      <UserForm mode="create" roleOptions={ROLE_OPTIONS} fieldSchema={FIELD_SCHEMA} submitting={false} error="Email already exists" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByText(/email already exists/i)).toBeInTheDocument();
  });
});

describe("UserForm — mode='edit'", () => {
  it("pre-fills fields from initial prop", () => {
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser()}
        submitting={false}
        error=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/^meno$/i)).toHaveValue("Tibor");
    expect(screen.getByLabelText(/priezvisko/i)).toHaveValue("Rausch");
    expect(screen.getByLabelText(/používateľské meno/i)).toHaveValue("tibi");
    expect(screen.getByLabelText(/email/i)).toHaveValue("tibi@icc.sk");
  });

  it("username input is disabled in edit mode", () => {
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser()}
        submitting={false}
        error=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/používateľské meno/i)).toBeDisabled();
  });

  it("shows Active checkbox in edit mode (reflecting initial)", () => {
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser({ is_active: false })}
        submitting={false}
        error=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const cb = screen.getByLabelText(/aktívny/i) as HTMLInputElement;
    expect(cb).toBeInTheDocument();
    expect(cb.checked).toBe(false);
  });

  it("password is optional — submit succeeds with empty password", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser()}
        submitting={false}
        error=""
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /uložiť/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ password: "" }),
    );
  });

  it("password validation same min 5 — disables Save when password filled but < 5", () => {
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser()}
        submitting={false}
        error=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/heslo/i), { target: { value: "abc" } });
    expect(screen.getByRole("button", { name: /uložiť/i })).toBeDisabled();
  });

  it("submit button reads 'Save' in edit mode (not 'Create')", () => {
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser()}
        submitting={false}
        error=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /uložiť/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /vytvoriť/i })).not.toBeInTheDocument();
  });

  it("submits with full payload including changed password and is_active", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserForm
        mode="edit"
        roleOptions={ROLE_OPTIONS}
        fieldSchema={FIELD_SCHEMA}
        initial={mkUser()}
        submitting={false}
        error=""
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/^meno$/i), { target: { value: "Tiborko" } });
    fireEvent.change(screen.getByLabelText(/heslo/i), { target: { value: "newone" } });
    fireEvent.click(screen.getByLabelText(/aktívny/i));
    fireEvent.click(screen.getByRole("button", { name: /uložiť/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({
      username: "tibi",
      email: "tibi@icc.sk",
      password: "newone",
      role: "ha",
      first_name: "Tiborko",
      last_name: "Rausch",
      telegram_chat_id: "",
      is_active: false,
    });
  });
});
