import { useEffect, useMemo, useState } from "react";
import { listUsersApi, createUserApi, updateUserApi, deleteUserApi, changePasswordApi } from "@/services/api/users";
import {
  listSystemSettingsApi,
  updateSystemSettingApi,
} from "@/services/api/systemSettings";
import {
  listUserAgentSettingsApi,
  upsertUserAgentSettingApi,
} from "@/services/api/userAgentSettings";
import { useAuthStore } from "@/store/authStore";
import { UserForm, type UserFormData } from "@/components/UserForm";
import { ROLE_LABELS } from "@/components/cockpit/labels";
import type { UserRead } from "@/types/user";
import type { SystemSettingRead } from "@/types/system_setting";
import type {
  AgentEffort,
  AgentModel,
  PipelineAgentRole,
} from "@/types/user_agent_setting";

// ─── Helpers ──────────────────────────────────────────────────────────────────

type SettingsTab = "system" | "agents" | "users" | "sessions";

// ── Agenti tab (CR-NS-040): per-role model/effort the cockpit applies at dispatch ──
// Labels come from the canonical ROLE_LABELS (labels.ts, CR-NS-018) — single source of
// truth shared with the pipeline board, so the role names never drift out of Slovak.
const AGENT_ROLES: { id: PipelineAgentRole; label: string }[] = [
  { id: "coordinator", label: ROLE_LABELS.coordinator },
  { id: "designer", label: ROLE_LABELS.designer },
  { id: "customer", label: ROLE_LABELS.customer },
  { id: "implementer", label: ROLE_LABELS.implementer },
  { id: "auditor", label: ROLE_LABELS.auditor },
];

const AGENT_MODELS: { id: AgentModel; label: string }[] = [
  { id: "claude-opus-4-8", label: "Opus 4.8" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6" },
  { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
];

// The 5 levels `claude --effort` accepts (no ultracode — see CR-NS-040 Effort policy).
const AGENT_EFFORTS: AgentEffort[] = ["low", "medium", "high", "xhigh", "max"];

interface AgentDraft {
  model: AgentModel | "";
  effort: AgentEffort | "";
}

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
  // E1 chrome unification (CR-NS-067): the dark-mode toggle moved to the top-bar
  // (the NEX Inbox vzor) — the Settings "Vzhľad" tab is retired.
  const [tab, setTab] = useState<SettingsTab>("system");

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

  // Agenti — per-role model/effort config for the CURRENT user (CR-NS-040). Draft/save like System.
  const [agentDrafts, setAgentDrafts] = useState<Record<string, AgentDraft>>({});
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [agentsLoadError, setAgentsLoadError] = useState("");
  const [savingRole, setSavingRole] = useState<string | null>(null);
  const [agentSaveErrors, setAgentSaveErrors] = useState<Record<string, string>>({});
  const [flashRole, setFlashRole] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== "agents" || agentsLoaded) return;
    listUserAgentSettingsApi()
      .then((rows) => {
        const initial: Record<string, AgentDraft> = {};
        for (const r of AGENT_ROLES) initial[r.id] = { model: "", effort: "" };
        for (const row of rows) {
          initial[row.agent_role] = { model: row.model ?? "", effort: row.effort ?? "" };
        }
        setAgentDrafts(initial);
        setAgentsLoaded(true);
      })
      .catch(() => setAgentsLoadError("Nepodarilo sa načítať konfiguráciu agentov."));
  }, [tab, agentsLoaded]);

  async function handleSaveAgentSetting(role: PipelineAgentRole) {
    const draft = agentDrafts[role] ?? { model: "", effort: "" };
    setSavingRole(role);
    setAgentSaveErrors((prev) => ({ ...prev, [role]: "" }));
    try {
      const saved = await upsertUserAgentSettingApi(role, {
        model: draft.model || null,
        effort: draft.effort || null,
      });
      setAgentDrafts((prev) => ({
        ...prev,
        [role]: { model: saved.model ?? "", effort: saved.effort ?? "" },
      }));
      setFlashRole(role);
      setTimeout(() => setFlashRole((r) => (r === role ? null : r)), 2000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Neznáma chyba.";
      setAgentSaveErrors((prev) => ({ ...prev, [role]: msg }));
    } finally {
      setSavingRole(null);
    }
  }

  // Users
  const [users, setUsers] = useState<UserRead[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [roleFilter, setRoleFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const [showNewForm, setShowNewForm] = useState(false);

  // Create / edit / delete state. The form fields themselves live inside
  // <UserForm /> — parent only tracks which flow is active and the
  // in-flight + error state for the API call.
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const [editingUser, setEditingUser] = useState<UserRead | null>(null);
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState("");

  // Inline delete confirmation: which row is currently asking "Áno/Nie?"
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

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

  async function handleCreateUser(data: UserFormData) {
    setCreating(true);
    setCreateError("");
    try {
      const u = await createUserApi({
        username: data.username,
        email: data.email,
        password: data.password,
        role: data.role,
        first_name: data.first_name || null,
        last_name: data.last_name || null,
        telegram_chat_id: data.telegram_chat_id || null,
      });
      setUsers((prev) => [u, ...prev]);
      setShowNewForm(false);
    } catch (e) {
      // Surface backend's specific error (e.g. "password too short",
      // "username already exists") instead of a generic message.
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

  function handleEditClick(u: UserRead) {
    setEditingUser(u);
    setEditError("");
    // Close the create form + delete confirm if either is open.
    setShowNewForm(false);
    setConfirmingDeleteId(null);
  }

  async function handleSaveEdit(data: UserFormData) {
    if (!editingUser) return;
    setEditing(true);
    setEditError("");
    try {
      // PATCH first — profile fields update independently of password.
      const updated = await updateUserApi(editingUser.id, {
        first_name: data.first_name || null,
        last_name: data.last_name || null,
        telegram_chat_id: data.telegram_chat_id || null,
        email: data.email,
        role: data.role,
        is_active: data.is_active,
      });
      // Optional password rotation. Empty input = keep current.
      // Done after PATCH so a failing PATCH doesn't leave the user
      // with a rotated password but stale profile.
      if (data.password) {
        await changePasswordApi(editingUser.id, data.password);
      }
      setUsers((prev) => prev.map((x) => x.id === updated.id ? updated : x));
      setEditingUser(null);
    } catch (e) {
      const msg =
        e instanceof Error && e.message
          ? `Nepodarilo sa uložiť zmeny: ${e.message}`
          : "Nepodarilo sa uložiť zmeny.";
      setEditError(msg);
    } finally {
      setEditing(false);
    }
  }

  async function handleConfirmDelete(id: string) {
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteUserApi(id);
      setUsers((prev) => prev.filter((x) => x.id !== id));
      setConfirmingDeleteId(null);
    } catch (e) {
      // Common case: backend returns 409 when the user is FK-referenced
      // by projects/bugs/etc. Surface the message + soft-disable hint.
      const msg =
        e instanceof Error && e.message
          ? `Nedá sa vymazať: ${e.message}. Skús miesto toho deaktivovať.`
          : "Nedá sa vymazať. Skús miesto toho deaktivovať.";
      setDeleteError(msg);
      setConfirmingDeleteId(null);
    } finally {
      setDeleting(false);
    }
  }

  const TABS: { id: SettingsTab; label: string }[] = [
    { id: "system", label: "Systém" },
    { id: "agents", label: "Agenti" },
    { id: "users", label: "Používatelia" },
    { id: "sessions", label: "Relácie" },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-slate-800 flex items-center justify-between">
        <h1 className="text-base font-bold text-slate-100">Nastavenia</h1>
        {user && (
          <span className="text-xs text-slate-600">
            Prihlásený ako{" "}
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
                                  <span className="text-slate-600">Predvolená hodnota.</span>
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

        {/* ── Agenti (per-user model/effort, CR-NS-040) ── */}
        {tab === "agents" && (
          <div className="p-6 max-w-3xl">
            <h2 className="text-sm font-semibold text-slate-300 mb-1">Agenti — model a effort</h2>
            <p className="text-xs text-slate-600 mb-4">
              Tvoja per-rola konfigurácia, ktorú cockpit aplikuje pri dispatchi agentov v <strong>tvojich</strong>{" "}
              projektoch (<code>--model</code> / <code>--effort</code>). Nenastavené pole = predvolené správanie
              (CLI default).
            </p>
            {agentsLoadError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400 mb-4">
                {agentsLoadError}
              </div>
            )}
            <div className="rounded-lg border border-slate-700 bg-slate-900 divide-y divide-slate-800">
              {AGENT_ROLES.map((r) => {
                const draft = agentDrafts[r.id] ?? { model: "", effort: "" };
                const saving = savingRole === r.id;
                const err = agentSaveErrors[r.id];
                return (
                  <div key={r.id} className="p-4">
                    <div className="flex items-start justify-between gap-4 mb-2">
                      <div>
                        <div className="text-sm font-medium text-slate-200">{r.label}</div>
                        {r.id === "coordinator" && !draft.effort && (
                          <div className="text-[11px] text-slate-500 mt-0.5">
                            Predvolený effort: <span className="font-mono">max</span>
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => handleSaveAgentSetting(r.id)}
                        disabled={saving}
                        className="shrink-0 px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed rounded transition-colors"
                      >
                        {saving ? "Ukladám…" : "Uložiť"}
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <label className="block">
                        <span className="text-[10px] text-slate-600 uppercase tracking-widest">Model</span>
                        <select
                          value={draft.model}
                          onChange={(e) =>
                            setAgentDrafts((prev) => ({
                              ...prev,
                              [r.id]: { ...draft, model: e.target.value as AgentModel | "" },
                            }))
                          }
                          className="mt-1 w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary-500"
                        >
                          <option value="">— Predvolený —</option>
                          {AGENT_MODELS.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block">
                        <span className="text-[10px] text-slate-600 uppercase tracking-widest">Úroveň</span>
                        <select
                          value={draft.effort}
                          onChange={(e) =>
                            setAgentDrafts((prev) => ({
                              ...prev,
                              [r.id]: { ...draft, effort: e.target.value as AgentEffort | "" },
                            }))
                          }
                          className="mt-1 w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary-500"
                        >
                          <option value="">— Predvolený —</option>
                          {AGENT_EFFORTS.map((ef) => (
                            <option key={ef} value={ef}>
                              {ef}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <div className="mt-2 text-[11px] flex items-center gap-2">
                      {flashRole === r.id && <span className="text-green-400">✓ Uložené</span>}
                      {err && <span className="text-red-400">{err}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Users ── */}
        {tab === "users" && (
          <div className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-300">Správa používateľov</h2>
              <button
                onClick={() => { setShowNewForm((v) => !v); setEditingUser(null); setConfirmingDeleteId(null); }}
                className="flex items-center gap-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                Nový používateľ
              </button>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-3 mb-3">
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-xs text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-primary-500"
              >
                <option value="">Všetky role</option>
                <option value="ri">ri — Director</option>
                <option value="ha">ha — Medior</option>
                <option value="shu">shu — Junior</option>
              </select>
              <select
                value={activeFilter}
                onChange={(e) => setActiveFilter(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-xs text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-primary-500"
              >
                <option value="">Akýkoľvek stav</option>
                <option value="active">Len aktívni</option>
                <option value="inactive">Len neaktívni</option>
              </select>
              <span className="ml-auto text-xs text-slate-600">
                {usersLoading ? "Načítavam…" : `${users.length} používateľov`}
              </span>
            </div>

            {/* Table */}
            <div className="rounded-xl border border-slate-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/80">
                  <tr className="text-[10px] uppercase tracking-widest text-slate-600">
                    <th className="px-4 py-2.5 text-left font-semibold">Meno</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Používateľské meno</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Email</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Rola</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Stav</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Akcie</th>
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
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">aktívny</span>
                        ) : (
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-400">neaktívny</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {confirmingDeleteId === u.id ? (
                          <div className="flex items-center justify-end gap-2 text-xs">
                            <span className="text-slate-400">Naozaj vymazať?</span>
                            <button
                              onClick={() => handleConfirmDelete(u.id)}
                              disabled={deleting}
                              className="px-2 py-0.5 text-red-400 border border-red-500/40 rounded hover:bg-red-500/10 disabled:opacity-40"
                            >
                              Áno
                            </button>
                            <button
                              onClick={() => setConfirmingDeleteId(null)}
                              disabled={deleting}
                              className="px-2 py-0.5 text-slate-400 border border-slate-700 rounded hover:bg-slate-800"
                            >
                              Nie
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-3">
                            {/* Edit (pencil) */}
                            <button
                              onClick={() => handleEditClick(u)}
                              title="Upraviť"
                              className="text-slate-500 hover:text-slate-200 transition-colors"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                              </svg>
                            </button>
                            {/* Delete (trash) */}
                            <button
                              onClick={() => { setConfirmingDeleteId(u.id); setDeleteError(""); }}
                              title="Vymazať"
                              className="text-slate-500 hover:text-red-400 transition-colors"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                            </button>
                            {/* Existing toggle (preserved per Director directive) */}
                            <button
                              onClick={() => handleToggleActive(u)}
                              className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                            >
                              {u.is_active ? "Deaktivovať" : "Aktivovať"}
                            </button>
                          </div>
                        )}
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

            {/* Delete error banner (shown after a failed DELETE — e.g. 409
                FK conflict). Dismiss on next action. */}
            {deleteError && (
              <div className="mt-3 text-xs text-red-400 rounded bg-red-500/10 border border-red-500/20 px-3 py-2 flex items-center justify-between">
                <span>{deleteError}</span>
                <button onClick={() => setDeleteError("")} className="text-red-400 hover:text-red-300 ml-2">×</button>
              </div>
            )}

            {/* Edit user form — same UserForm component as create, mode-driven. */}
            {editingUser && (
              <UserForm
                key={`edit-${editingUser.id}`}
                mode="edit"
                initial={editingUser}
                submitting={editing}
                error={editError}
                onSubmit={handleSaveEdit}
                onCancel={() => { setEditingUser(null); setEditError(""); }}
              />
            )}

            {/* New user form */}
            {showNewForm && (
              <UserForm
                mode="create"
                submitting={creating}
                error={createError}
                onSubmit={handleCreateUser}
                onCancel={() => { setShowNewForm(false); setCreateError(""); }}
              />
            )}
          </div>
        )}

        {/* ── Sessions ── */}
        {tab === "sessions" && (
          <div className="p-6">
            <h2 className="text-sm font-semibold text-slate-300 mb-1">Relácie používateľa</h2>
            <p className="text-xs text-slate-600 mb-4">Kotvy životného cyklu JWT pre používateľa. Vymazanie relácie zneplatní všetky zostávajúce tokeny.</p>
            <div className="rounded-xl border border-slate-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/80">
                  <tr className="text-[10px] uppercase tracking-widest text-slate-600">
                    <th className="px-4 py-2.5 text-left font-semibold">Používateľ</th>
                    <th className="px-4 py-2.5 text-left font-semibold">ID relácie</th>
                    <th className="px-4 py-2.5 text-right font-semibold">tv</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Naposledy videný</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Akcie</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  <tr className="hover:bg-slate-800/40 transition-colors">
                    <td className="px-4 py-3 text-sm font-medium text-slate-200">{user?.username ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-[10px] text-slate-500">{user?.id?.slice(0, 22) ?? "—"}…</td>
                    <td className="px-4 py-3 text-right font-mono text-xs text-slate-400">—</td>
                    <td className="px-4 py-3 text-xs text-slate-500">—</td>
                    <td className="px-4 py-3 text-right">
                      <span className="text-xs text-slate-700">aktuálna relácia</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-[11px] text-slate-700 mt-3">Koncový bod správy relácií zatiaľ nie je implementovaný v backende.</p>
          </div>
        )}
      </div>
    </div>
  );
}
