import { useEffect, useState } from "react";
import { listUsersApi, createUserApi, updateUserApi } from "@/services/api/users";
import {
  getSystemSettingApi,
  updateSystemSettingApi,
} from "@/services/api/systemSettings";
import { useAuthStore } from "@/store/authStore";
import type { UserRead, UserRole } from "@/types/user";

// ─── Helpers ──────────────────────────────────────────────────────────────────

type SettingsTab = "appearance" | "icc" | "users" | "sessions";

function roleCls(role: string) {
  if (role === "ri") return "text-indigo-400";
  if (role === "ha") return "text-green-400";
  return "text-amber-400";
}

// ─── SettingsPage ─────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<SettingsTab>("appearance");

  // Appearance
  const [lang, setLang] = useState<"sk" | "en">("sk");

  // ICC — github_org
  const [githubOrg, setGithubOrg] = useState("");
  const [githubOrgLoaded, setGithubOrgLoaded] = useState(false);
  const [githubOrgIsDefault, setGithubOrgIsDefault] = useState(true);
  const [githubOrgSaving, setGithubOrgSaving] = useState(false);
  const [githubOrgError, setGithubOrgError] = useState("");
  const [githubOrgSavedFlash, setGithubOrgSavedFlash] = useState(false);

  const isRi = user?.role === "ri";

  // Load github_org whenever the ICC tab becomes visible (first time only).
  useEffect(() => {
    if (tab !== "icc" || githubOrgLoaded) return;
    getSystemSettingApi("github_org")
      .then((s) => {
        setGithubOrg(s.value);
        setGithubOrgIsDefault(s.is_default);
        setGithubOrgLoaded(true);
      })
      .catch(() => setGithubOrgError("Nepodarilo sa načítať nastavenie."));
  }, [tab, githubOrgLoaded]);

  async function handleSaveGithubOrg() {
    if (!githubOrg.trim()) return;
    setGithubOrgSaving(true);
    setGithubOrgError("");
    try {
      const updated = await updateSystemSettingApi("github_org", githubOrg.trim());
      setGithubOrg(updated.value);
      setGithubOrgIsDefault(updated.is_default);
      setGithubOrgSavedFlash(true);
      setTimeout(() => setGithubOrgSavedFlash(false), 2000);
    } catch {
      setGithubOrgError("Nepodarilo sa uložiť. Skontroluj, či máš rolu ri.");
    } finally {
      setGithubOrgSaving(false);
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
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

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
    setCreating(true);
    setCreateError("");
    try {
      const u = await createUserApi({ username: newUsername, email: newEmail, password: newPassword, role: newRole });
      setUsers((prev) => [u, ...prev]);
      setShowNewForm(false);
      setNewUsername(""); setNewEmail(""); setNewPassword(""); setNewRole("shu");
    } catch {
      setCreateError("Nepodarilo sa vytvoriť používateľa.");
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
    { id: "icc", label: "ICC" },
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

        {/* ── ICC ── */}
        {tab === "icc" && (
          <div className="p-6 max-w-lg">
            <h2 className="text-sm font-semibold text-slate-300 mb-1">ICC integrations</h2>
            <p className="text-xs text-slate-600 mb-4">
              Runtime-mutable ICC-wide settings. Editable only by ri role.
            </p>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-3">
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-widest mb-1">
                  GitHub organization
                </label>
                <p className="text-xs text-slate-600 mb-2">
                  Used to auto-fill the repository URL on the new-project
                  form as <code className="text-slate-400">{"{github_org}/{slug}"}</code>.
                </p>
                <div className="flex gap-2 items-center">
                  <input
                    type="text"
                    value={githubOrg}
                    onChange={(e) => setGithubOrg(e.target.value)}
                    disabled={!isRi || !githubOrgLoaded}
                    placeholder="rauschiccsk"
                    className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-primary-500 disabled:opacity-50"
                  />
                  {isRi && (
                    <button
                      onClick={handleSaveGithubOrg}
                      disabled={githubOrgSaving || !githubOrg.trim() || !githubOrgLoaded}
                      className="px-3 py-2 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 rounded-lg transition-colors"
                    >
                      {githubOrgSaving ? "Ukladám…" : "Save"}
                    </button>
                  )}
                </div>
                <div className="mt-2 text-[11px] flex items-center gap-2">
                  {githubOrgIsDefault && githubOrgLoaded && (
                    <span className="text-slate-600">Using default value.</span>
                  )}
                  {!githubOrgIsDefault && githubOrgLoaded && (
                    <span className="text-slate-500">Stored override.</span>
                  )}
                  {githubOrgSavedFlash && <span className="text-green-400">Uložené.</span>}
                  {githubOrgError && <span className="text-red-400">{githubOrgError}</span>}
                  {!isRi && (
                    <span className="ml-auto text-slate-700">Read-only — ri role required.</span>
                  )}
                </div>
              </div>
            </div>
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
                    <th className="px-4 py-2.5 text-left font-semibold">Username</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Email</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Role</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Status</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {users.map((u) => (
                    <tr key={u.id} className="hover:bg-slate-800/40 transition-colors">
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
                  ))}
                  {!usersLoading && users.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-6 text-center text-xs text-slate-600">Žiadni používatelia</td>
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
                      placeholder="min 8 characters"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    />
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
                    onClick={() => { setShowNewForm(false); setCreateError(""); }}
                    className="px-3 py-1.5 text-xs text-slate-400 border border-slate-700 rounded-lg hover:bg-slate-800 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleCreateUser}
                    disabled={creating || !newUsername || !newEmail || !newPassword}
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
