import { useEffect, useMemo, useState } from "react";
import { listUsersApi, createUserApi, updateUserApi } from "@/services/api/users";
import {
  listSystemSettingsApi,
  updateSystemSettingApi,
} from "@/services/api/systemSettings";
import { useAuthStore } from "@/store/authStore";
import type { UserRead, UserRole } from "@/types/user";
import type { SystemSettingRead } from "@/types/system_setting";

// ─── Helpers ──────────────────────────────────────────────────────────────────

type SettingsTab = "appearance" | "system" | "users" | "sessions";

function roleCls(role: string) {
  if (role === "ri") return "text-indigo-400";
  if (role === "ha") return "text-green-400";
  return "text-amber-400";
}

/**
 * System-settings categories. Every ``system_settings`` key whose
 * prefix matches one of ``prefixes`` is rendered under the category.
 * Keys that fit none of the prefixes fall through into the trailing
 * "Ostatné" bucket so forward-compat additions stay visible.
 */
const SETTINGS_CATEGORIES: {
  id: string;
  label: string;
  description: string;
  prefixes: string[];
}[] = [
  {
    id: "pipeline",
    label: "Pipeline / AI",
    description:
      "Timeouty pre Claude CLI (chat, generovanie dokumentácie, task plan) a limity kontextu pre AI prompty.",
    prefixes: ["claude_", "conversation_", "design_doc_"],
  },
  {
    id: "github",
    label: "GitHub",
    description:
      "Integrácia s GitHubom — cieľová organizácia a sieťové nastavenia volaní do GitHub API.",
    prefixes: ["github_"],
  },
  {
    id: "auth",
    label: "Autentifikácia",
    description: "JWT tokeny a súvisiace časové obmedzenia.",
    prefixes: ["access_token_"],
  },
  {
    id: "ports",
    label: "Port Registry (ICC D-020)",
    description:
      "Rozsah portov prideľovaných projektom a veľkosť per-project bloku. Zmena má dosah len na nové projekty.",
    prefixes: ["port_"],
  },
  {
    id: "paths",
    label: "Cesty a šablóny",
    description:
      "Defaultné šablóny pre ``source_path`` a KB cestu pri vytvorení projektu. ``{slug}`` sa nahradí slugom projektu.",
    prefixes: ["default_source_path", "default_kb_path"],
  },
];

function _classifyKey(key: string): string {
  for (const cat of SETTINGS_CATEGORIES) {
    if (cat.prefixes.some((p) => key.startsWith(p))) return cat.id;
  }
  return "other";
}

function _inputTypeFor(valueType: string): "number" | "checkbox" | "text" {
  if (valueType === "int" || valueType === "float") return "number";
  if (valueType === "bool") return "checkbox";
  return "text";
}

// ─── SettingsPage ─────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<SettingsTab>("appearance");

  // Appearance
  const [lang, setLang] = useState<"sk" | "en">("sk");

  // System settings — loaded once the System tab becomes visible. The
  // per-row editor state (draft + saving + flash) lives alongside the
  // settings list to keep handlers simple.
  const [settings, setSettings] = useState<SystemSettingRead[]>([]);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const [flashKey, setFlashKey] = useState<string | null>(null);

  const isRi = user?.role === "ri";

  useEffect(() => {
    if (tab !== "system" || settingsLoaded) return;
    listSystemSettingsApi()
      .then((rows) => {
        setSettings(rows);
        const initialDrafts: Record<string, string> = {};
        for (const r of rows) initialDrafts[r.key] = r.value;
        setDrafts(initialDrafts);
        setSettingsLoaded(true);
      })
      .catch(() => setSettingsLoadError("Nepodarilo sa načítať nastavenia."));
  }, [tab, settingsLoaded]);

  const groupedSettings = useMemo(() => {
    const groups: Record<string, SystemSettingRead[]> = {};
    for (const s of settings) {
      const catId = _classifyKey(s.key);
      (groups[catId] ||= []).push(s);
    }
    for (const list of Object.values(groups)) {
      list.sort((a, b) => a.key.localeCompare(b.key));
    }
    return groups;
  }, [settings]);

  async function handleSaveSetting(key: string) {
    const draft = (drafts[key] ?? "").toString();
    if (!draft.trim() && draft !== "0" && draft.toLowerCase() !== "false") return;
    setSavingKey(key);
    setSaveErrors((prev) => ({ ...prev, [key]: "" }));
    try {
      const updated = await updateSystemSettingApi(key, draft);
      setSettings((prev) => prev.map((s) => (s.key === key ? updated : s)));
      setDrafts((prev) => ({ ...prev, [key]: updated.value }));
      setFlashKey(key);
      setTimeout(() => setFlashKey((k) => (k === key ? null : k)), 2000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Neznáma chyba.";
      setSaveErrors((prev) => ({ ...prev, [key]: msg }));
    } finally {
      setSavingKey(null);
    }
  }

  // Users
  const [users, setUsers] = useState<UserRead[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [roleFilter, setRoleFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const [showNewForm, setShowNewForm] = useState(false);

  // New user form
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("shu");
  const [newFirstName, setNewFirstName] = useState("");
  const [newLastName, setNewLastName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  /** Minimum password length — mirrors backend Pydantic constraint
   *  (Director directive 2026-05-13, internal app). */
  const PASSWORD_MIN_LENGTH = 5;

  useEffect(() => {
    if (tab !== "users") return;
    setUsersLoading(true);
    const params: { role?: string; is_active?: boolean } = {};
    if (roleFilter) params.role = roleFilter;
    if (activeFilter === "active") params.is_active = true;
    if (activeFilter === "inactive") params.is_active = false;
    listUsersApi({ limit: 100, ...params })
      .then((res) => setUsers(res.items))
      .finally(() => setUsersLoading(false));
  }, [tab, roleFilter, activeFilter]);

  async function handleCreateUser() {
    if (!newUsername || !newEmail || !newPassword) return;
    if (newPassword.length < PASSWORD_MIN_LENGTH) {
      setCreateError(`Heslo musí mať aspoň ${PASSWORD_MIN_LENGTH} znakov.`);
      return;
    }
    setCreating(true);
    setCreateError("");
    try {
      const u = await createUserApi({
        username: newUsername,
        email: newEmail,
        password: newPassword,
        role: newRole,
        first_name: newFirstName || null,
        last_name: newLastName || null,
      });
      setUsers((prev) => [u, ...prev]);
      setShowNewForm(false);
      setNewUsername("");
      setNewEmail("");
      setNewPassword("");
      setNewRole("shu");
      setNewFirstName("");
      setNewLastName("");
    } catch (e) {
      // Surface backend's specific error (e.g. "password too short",
      // "username already exists") instead of a generic message.
      // ApiError has .message (string); other errors fall back.
      const msg =
        e instanceof Error && e.message
          ? `Nepodarilo sa vytvoriť používateľa: ${e.message}`
          : "Nepodarilo sa vytvoriť používateľa.";
      setCreateError(msg);
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(u: UserRead) {
    try {
      const updated = await updateUserApi(u.id, { is_active: !u.is_active });
      setUsers((prev) => prev.map((x) => x.id === u.id ? updated : x));
    } catch { /* ignore */ }
  }

  const TABS: { id: SettingsTab; label: string }[] = [
    { id: "appearance", label: "Appearance" },
    { id: "system", label: "System" },
    { id: "users", label: "Users" },
    { id: "sessions", label: "Sessions" },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-slate-800 flex items-center justify-between">
        <h1 className="text-base font-bold text-slate-100">Settings</h1>
        {user && (
          <span className="text-xs text-slate-600">
            Signed in as{" "}
            <span className="text-slate-400 font-medium">{user.username}</span>
            {" · "}
            <span className={`font-mono text-[11px] ${roleCls(user.role)}`}>{user.role}</span>
          </span>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex-shrink-0 flex gap-0 border-b border-slate-800 px-6">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.id
                ? "border-primary-500 text-primary-400"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab panels */}
      <div className="flex-1 overflow-y-auto">

        {/* ── Appearance ── */}
        {tab === "appearance" && (
          <div className="p-6 max-w-lg">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Appearance</h2>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 mb-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">Dark mode</div>
                  <div className="text-xs text-slate-500 mt-0.5">Use dark theme across the application</div>
                </div>
                <button className="relative inline-flex h-6 w-11 items-center rounded-full bg-primary-600 transition-colors focus:outline-none">
                  <span className="inline-block h-4 w-4 transform rounded-full bg-white transition-transform translate-x-6" />
                </button>
              </div>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-3">
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-1">Language</div>
              <div className="flex gap-2">
                <button
                  onClick={() => setLang("sk")}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                    lang === "sk"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-slate-700 text-slate-500 hover:text-slate-300"
                  }`}
                >
                  Slovenčina
                </button>
                <button
                  onClick={() => setLang("en")}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                    lang === "en"
                      ? "border-primary-500 bg-primary-500/10 text-primary-400"
                      : "border-slate-700 text-slate-500 hover:text-slate-300"
                  }`}
                >
                  English
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── System settings ── */}
        {tab === "system" && (
          <div className="p-6 max-w-3xl">
            <h2 className="text-sm font-semibold text-slate-300 mb-1">Systémové nastavenia</h2>
            <p className="text-xs text-slate-600 mb-4">
              Runtime-mutable ICC-wide settings. Editovateľné iba rolou <code>ri</code>; zmeny sa prejavia do 30 s (interná cache TTL).
            </p>
            {settingsLoadError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400 mb-4">
                {settingsLoadError}
              </div>
            )}
            {!settingsLoaded && !settingsLoadError && (
              <div className="text-xs text-slate-600">Načítavam…</div>
            )}
            {settingsLoaded && (
              <div className="space-y-6">
                {[...SETTINGS_CATEGORIES, { id: "other", label: "Ostatné", description: "", prefixes: [] }].map((cat) => {
                  const rows = groupedSettings[cat.id] ?? [];
                  if (rows.length === 0) return null;
                  return (
                    <section key={cat.id}>
                      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-1">{cat.label}</h3>
                      {cat.description && (
                        <p className="text-[11px] text-slate-600 mb-2">{cat.description}</p>
                      )}
                      <div className="rounded-lg border border-slate-700 bg-slate-900 divide-y divide-slate-800">
                        {rows.map((s) => {
                          const draft = drafts[s.key] ?? s.value;
                          const dirty = draft !== s.value;
                          const inputType = _inputTypeFor(s.value_type);
                          const saving = savingKey === s.key;
                          const err = saveErrors[s.key];
                          return (
                            <div key={s.key} className="p-4">
                              <div className="flex items-start justify-between gap-4 mb-1">
                                <div className="min-w-0">
                                  <div className="text-sm font-medium text-slate-200 font-mono">{s.key}</div>
                                  <div className="text-[10px] text-slate-600 uppercase tracking-widest mt-0.5">{s.value_type}</div>
                                </div>
                                {isRi && (
                                  <button
                                    onClick={() => handleSaveSetting(s.key)}
                                    disabled={saving || !dirty}
                                    className="shrink-0 px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed rounded transition-colors"
                                  >
                                    {saving ? "Ukladám…" : dirty ? "Uložiť" : "Uložené"}
                                  </button>
                                )}
                              </div>
                              {s.description && (
                                <p className="text-xs text-slate-500 mb-2 leading-relaxed">{s.description}</p>
                              )}
                              {inputType === "checkbox" ? (
                                <label className="flex items-center gap-2 text-xs text-slate-300">
                                  <input
                                    type="checkbox"
                                    checked={draft.toLowerCase() === "true" || draft === "1"}
                                    onChange={(e) =>
                                      setDrafts((prev) => ({ ...prev, [s.key]: e.target.checked ? "true" : "false" }))
                                    }
                                    disabled={!isRi}
                                    className="rounded border-slate-700 bg-slate-800 text-primary-500 focus:ring-primary-500 disabled:opacity-50"
                                  />
                                  <span className="font-mono">{draft}</span>
                                </label>
                              ) : (
                                <input
                                  type={inputType}
                                  value={draft}
                                  onChange={(e) => setDrafts((prev) => ({ ...prev, [s.key]: e.target.value }))}
                                  disabled={!isRi}
                                  step={s.value_type === "float" ? "any" : undefined}
                                  className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-primary-500 disabled:opacity-50"
                                />
                              )}
                              <div className="mt-2 text-[11px] flex items-center gap-2 flex-wrap">
                                {s.is_default ? (
                                  <span className="text-slate-600">Default hodnota.</span>
                                ) : (
                                  <span className="text-slate-500">
                                    Uložený override
                                    {s.updated_by_username && (
                                      <> — <span className="text-slate-400 font-medium">{s.updated_by_username}</span></>
                                    )}
                                    {s.updated_at && (
                                      <> · {new Date(s.updated_at).toLocaleString("sk-SK")}</>
                                    )}
                                  </span>
                                )}
                                {flashKey === s.key && <span className="text-green-400">✓ Uložené</span>}
                                {err && <span className="text-red-400">{err}</span>}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  );
                })}
                {!isRi && (
                  <p className="text-[11px] text-slate-700 italic">
                    Read-only — na úpravu je potrebná rola <code>ri</code>.
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Users ── */}
        {tab === "users" && (
          <div className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-300">User management</h2>
              <button
                onClick={() => setShowNewForm((v) => !v)}
                className="flex items-center gap-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                New user
              </button>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-3 mb-3">
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-xs text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-primary-500"
              >
                <option value="">All roles</option>
                <option value="ri">ri — Director</option>
                <option value="ha">ha — Medior</option>
                <option value="shu">shu — Junior</option>
              </select>
              <select
                value={activeFilter}
                onChange={(e) => setActiveFilter(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-xs text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-primary-500"
              >
                <option value="">Any status</option>
                <option value="active">Active only</option>
                <option value="inactive">Inactive only</option>
              </select>
              <span className="ml-auto text-xs text-slate-600">
                {usersLoading ? "Načítavam…" : `${users.length} users`}
              </span>
            </div>

            {/* Table */}
            <div className="rounded-xl border border-slate-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/80">
                  <tr className="text-[10px] uppercase tracking-widest text-slate-600">
                    <th className="px-4 py-2.5 text-left font-semibold">Name</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Username</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Email</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Role</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Status</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {users.map((u) => {
                    const fullName = [u.first_name, u.last_name]
                      .filter(Boolean)
                      .join(" ");
                    return (
                    <tr key={u.id} className="hover:bg-slate-800/40 transition-colors">
                      <td className="px-4 py-3 text-sm text-slate-300">
                        {fullName || <span className="text-slate-600">—</span>}
                      </td>
                      <td className="px-4 py-3 text-sm font-medium text-slate-200 font-mono">{u.username}</td>
                      <td className="px-4 py-3 text-xs text-slate-400">{u.email}</td>
                      <td className="px-4 py-3">
                        <span className={`text-[11px] font-mono font-medium ${roleCls(u.role)}`}>{u.role}</span>
                      </td>
                      <td className="px-4 py-3">
                        {u.is_active ? (
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">active</span>
                        ) : (
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-400">inactive</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => handleToggleActive(u)}
                          className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                        >
                          {u.is_active ? "Deaktivovať" : "Aktivovať"}
                        </button>
                      </td>
                    </tr>
                    );
                  })}
                  {!usersLoading && users.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-6 text-center text-xs text-slate-600">Žiadni používatelia</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* New user form */}
            {showNewForm && (
              <div className="mt-4 rounded-xl border border-slate-700 bg-slate-900 p-4">
                <h3 className="text-sm font-semibold text-slate-300 mb-3">Create user</h3>
                {createError && (
                  <div className="mb-3 text-xs text-red-400 rounded bg-red-500/10 border border-red-500/20 px-3 py-2">{createError}</div>
                )}
                <div className="grid grid-cols-2 gap-3 mb-3">
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">First name</label>
                    <input
                      type="text"
                      value={newFirstName}
                      onChange={(e) => setNewFirstName(e.target.value)}
                      placeholder="e.g. Tibor"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">Last name</label>
                    <input
                      type="text"
                      value={newLastName}
                      onChange={(e) => setNewLastName(e.target.value)}
                      placeholder="e.g. Rausch"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">Username *</label>
                    <input
                      type="text"
                      value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      placeholder="e.g. tibor"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">Email *</label>
                    <input
                      type="email"
                      value={newEmail}
                      onChange={(e) => setNewEmail(e.target.value)}
                      placeholder="e.g. tibor@isnex.ai"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">Password *</label>
                    <input
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder={`min ${PASSWORD_MIN_LENGTH} characters`}
                      className={`w-full bg-slate-800 border rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500 ${
                        newPassword && newPassword.length < PASSWORD_MIN_LENGTH
                          ? "border-red-500"
                          : "border-slate-700"
                      }`}
                    />
                    {newPassword && newPassword.length < PASSWORD_MIN_LENGTH && (
                      <div className="mt-1 text-[10px] text-red-400">
                        Heslo musí mať aspoň {PASSWORD_MIN_LENGTH} znakov ({newPassword.length}/{PASSWORD_MIN_LENGTH}).
                      </div>
                    )}
                  </div>
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">Role</label>
                    <select
                      value={newRole}
                      onChange={(e) => setNewRole(e.target.value as UserRole)}
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    >
                      <option value="shu">shu — Junior</option>
                      <option value="ha">ha — Medior</option>
                      <option value="ri">ri — Director</option>
                    </select>
                  </div>
                </div>
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={() => {
                      setShowNewForm(false);
                      setCreateError("");
                    }}
                    className="px-3 py-1.5 text-xs text-slate-400 border border-slate-700 rounded-lg hover:bg-slate-800 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleCreateUser}
                    disabled={
                      creating ||
                      !newUsername ||
                      !newEmail ||
                      !newPassword ||
                      newPassword.length < PASSWORD_MIN_LENGTH
                    }
                    className="px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 rounded-lg transition-colors"
                  >
                    {creating ? "Vytváram…" : "Create"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Sessions ── */}
        {tab === "sessions" && (
          <div className="p-6">
            <h2 className="text-sm font-semibold text-slate-300 mb-1">User Sessions</h2>
            <p className="text-xs text-slate-600 mb-4">Per-user JWT lifecycle anchors. Deleting a session invalidates all outstanding tokens.</p>
            <div className="rounded-xl border border-slate-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/80">
                  <tr className="text-[10px] uppercase tracking-widest text-slate-600">
                    <th className="px-4 py-2.5 text-left font-semibold">User</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Session ID</th>
                    <th className="px-4 py-2.5 text-right font-semibold">tv</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Last seen</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  <tr className="hover:bg-slate-800/40 transition-colors">
                    <td className="px-4 py-3 text-sm font-medium text-slate-200">{user?.username ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-[10px] text-slate-500">{user?.id?.slice(0, 22) ?? "—"}…</td>
                    <td className="px-4 py-3 text-right font-mono text-xs text-slate-400">—</td>
                    <td className="px-4 py-3 text-xs text-slate-500">—</td>
                    <td className="px-4 py-3 text-right">
                      <span className="text-xs text-slate-700">current session</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-[11px] text-slate-700 mt-3">Session management endpoint not yet implemented in backend.</p>
          </div>
        )}
      </div>
    </div>
  );
}
