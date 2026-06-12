/**
 * UserForm — single source of truth for both Create + Edit user flows
 * in Settings → Users. Replaces two near-identical inline JSX blocks
 * (~160 lines of duplicate markup) per CLEAN_CODE.md DRY directive
 * and Director feedback 2026-05-13.
 *
 * Mode-specific differences:
 *
 *   - mode="create": username editable, password required, no Active
 *     checkbox (always defaults to is_active=true on create).
 *   - mode="edit":   username disabled (login stability — see
 *     SettingsPage delete bug discussion), password optional (empty =
 *     keep current), Active checkbox shown.
 *
 * Shared: identical layout, identical Tailwind classes, password min 5
 * validation (mirrors backend Pydantic min_length=5 — Director
 * directive 2026-05-13, NEX Studio is internal).
 *
 * The component owns its internal form state. The parent receives the
 * collected ``UserFormData`` via ``onSubmit`` and decides which API
 * calls to issue (createUserApi vs updateUserApi + changePasswordApi).
 */

import { useState } from "react";
import type { UserRead, UserRole } from "@/types/user";

/** Minimum password length — mirrors backend Pydantic constraint
 *  (Director directive 2026-05-13, internal app). */
export const PASSWORD_MIN_LENGTH = 5;

export interface UserFormData {
  username: string;
  email: string;
  /** Create: required. Edit: empty string = keep current. */
  password: string;
  role: UserRole;
  first_name: string;
  last_name: string;
  /** Telegram chat_id for agent notifications (CR-NS-012). */
  telegram_chat_id: string;
  is_active: boolean;
}

export interface UserFormProps {
  mode: "create" | "edit";
  /** Required in edit mode — values pre-fill the form. */
  initial?: UserRead;
  /** Disables all inputs + submit button. Driven by the parent's
   *  in-flight API call. */
  submitting: boolean;
  /** Backend error message to display below the title. Empty = hidden. */
  error: string;
  /** Called with the collected form data when the user clicks Submit.
   *  Parent maps to the right API calls (create vs patch + change-password). */
  onSubmit: (data: UserFormData) => Promise<void> | void;
  onCancel: () => void;
}

function initialFromUser(user: UserRead | undefined): UserFormData {
  return {
    username: user?.username ?? "",
    email: user?.email ?? "",
    password: "",
    role: user?.role ?? "shu",
    first_name: user?.first_name ?? "",
    last_name: user?.last_name ?? "",
    telegram_chat_id: user?.telegram_chat_id ?? "",
    is_active: user?.is_active ?? true,
  };
}

export function UserForm({
  mode,
  initial,
  submitting,
  error,
  onSubmit,
  onCancel,
}: UserFormProps) {
  const isEdit = mode === "edit";
  const [data, setData] = useState<UserFormData>(() => initialFromUser(initial));

  const passwordTooShort =
    data.password.length > 0 && data.password.length < PASSWORD_MIN_LENGTH;

  // Submit is disabled when:
  //   - currently submitting (in-flight call)
  //   - email empty (always required)
  //   - create mode: username or password empty (both required)
  //   - any mode: password filled but below min length
  const submitDisabled =
    submitting ||
    !data.email ||
    (!isEdit && (!data.username || !data.password)) ||
    passwordTooShort;

  const title = isEdit
    ? `Upraviť používateľa · ${initial?.username ?? ""}`
    : "Vytvoriť používateľa";
  const submitLabel = isEdit
    ? submitting
      ? "Ukladám…"
      : "Uložiť"
    : submitting
      ? "Vytváram…"
      : "Vytvoriť";

  function update<K extends keyof UserFormData>(key: K, value: UserFormData[K]) {
    setData((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit() {
    if (submitDisabled) return;
    void onSubmit(data);
  }

  return (
    <div className="mt-4 rounded-xl border border-slate-700 bg-slate-900 p-4">
      <h3 className="text-sm font-semibold text-slate-300 mb-3">
        {isEdit ? (
          <>
            Upraviť používateľa ·{" "}
            <span className="font-mono text-slate-400">{initial?.username}</span>
          </>
        ) : (
          title
        )}
      </h3>

      {error && (
        <div className="mb-3 text-xs text-red-400 rounded bg-red-500/10 border border-red-500/20 px-3 py-2">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 mb-3">
        <div>
          <label htmlFor="uf-first-name" className="block text-xs text-slate-500 mb-1">Meno</label>
          <input
            id="uf-first-name"
            type="text"
            value={data.first_name}
            onChange={(e) => update("first_name", e.target.value)}
            placeholder="napr. Tibor"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
          />
        </div>

        <div>
          <label htmlFor="uf-last-name" className="block text-xs text-slate-500 mb-1">Priezvisko</label>
          <input
            id="uf-last-name"
            type="text"
            value={data.last_name}
            onChange={(e) => update("last_name", e.target.value)}
            placeholder="napr. Rausch"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
          />
        </div>

        <div>
          <label htmlFor="uf-username" className="block text-xs text-slate-500 mb-1">
            Používateľské meno {isEdit ? "" : "*"}
          </label>
          <input
            id="uf-username"
            type="text"
            value={data.username}
            onChange={(e) => update("username", e.target.value)}
            disabled={isEdit}
            title={isEdit ? "Používateľské meno sa po vytvorení nemení (zachováva login stabilitu)." : undefined}
            placeholder="napr. tibi"
            className={`w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-primary-500 ${
              isEdit
                ? "bg-slate-800/60 border-slate-700 text-slate-500 cursor-not-allowed"
                : "bg-slate-800 border-slate-700 text-slate-100"
            }`}
          />
        </div>

        <div>
          <label htmlFor="uf-email" className="block text-xs text-slate-500 mb-1">Email *</label>
          <input
            id="uf-email"
            type="email"
            value={data.email}
            onChange={(e) => update("email", e.target.value)}
            placeholder="napr. tibi@isnex.ai"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
          />
        </div>

        <div>
          <label htmlFor="uf-password" className="block text-xs text-slate-500 mb-1">
            {isEdit ? (
              <>
                Nové heslo{" "}
                <span className="text-slate-600">(nechaj prázdne ak nemeniť)</span>
              </>
            ) : (
              "Heslo *"
            )}
          </label>
          <input
            id="uf-password"
            type="password"
            value={data.password}
            onChange={(e) => update("password", e.target.value)}
            placeholder={`min. ${PASSWORD_MIN_LENGTH} znakov`}
            className={`w-full bg-slate-800 border rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500 ${
              passwordTooShort ? "border-red-500" : "border-slate-700"
            }`}
          />
          {passwordTooShort && (
            <div className="mt-1 text-[10px] text-red-400">
              Heslo musí mať aspoň {PASSWORD_MIN_LENGTH} znakov ({data.password.length}/{PASSWORD_MIN_LENGTH}).
            </div>
          )}
        </div>

        <div>
          <label htmlFor="uf-role" className="block text-xs text-slate-500 mb-1">Rola</label>
          <select
            id="uf-role"
            value={data.role}
            onChange={(e) => update("role", e.target.value as UserRole)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
          >
            <option value="shu">shu — Junior</option>
            <option value="ha">ha — Medior</option>
            <option value="ri">ri — Director</option>
          </select>
        </div>

        <div>
          <label htmlFor="uf-telegram" className="block text-xs text-slate-500 mb-1">
            Telegram chat_id
          </label>
          <input
            id="uf-telegram"
            type="text"
            value={data.telegram_chat_id}
            onChange={(e) => update("telegram_chat_id", e.target.value)}
            placeholder="napr. 123456789 (notifikácie agenta)"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
          />
        </div>

        {isEdit && (
          <div className="flex items-end">
            <label htmlFor="uf-active" className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
              <input
                id="uf-active"
                type="checkbox"
                checked={data.is_active}
                onChange={(e) => update("is_active", e.target.checked)}
                className="rounded bg-slate-800 border-slate-700"
              />
              Aktívny
            </label>
          </div>
        )}
      </div>

      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 text-xs text-slate-400 border border-slate-700 rounded-lg hover:bg-slate-800 transition-colors"
        >
          Zrušiť
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitDisabled}
          className="px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 rounded-lg transition-colors"
        >
          {submitLabel}
        </button>
      </div>
    </div>
  );
}
