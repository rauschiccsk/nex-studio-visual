import { useCallback, useEffect, useMemo, useState } from "react";
import {
  SettingsShell,
  SystemSettingsPanel,
  AgentsPanel,
  UsersPanel,
  SessionsPanel,
  type SettingsKitConfig,
  type SettingsCategory,
  type UserFieldSchema,
  type AgentDraft,
  type UserFormData,
  type UserRead as KitUserRead,
  type UserSessionRead,
} from "nex-shared";
import {
  listUsersApi,
  createUserApi,
  updateUserApi,
  deleteUserApi,
  changePasswordApi,
} from "@/services/api/users";
import {
  listSystemSettingsApi,
  updateSystemSettingApi,
} from "@/services/api/systemSettings";
import {
  listUserAgentSettingsApi,
  upsertUserAgentSettingApi,
} from "@/services/api/userAgentSettings";
import {
  listUserSessionsApi,
  deleteUserSessionApi,
} from "@/services/api/userSessions";
import { useAuthStore } from "@/store/authStore";
import { ROLE_LABELS } from "@/components/cockpit/labels";
import type { UserRole, UserRead } from "@/types/user";
import type { SystemSettingRead } from "@/types/system_setting";
import type {
  AgentEffort,
  AgentModel,
  PipelineAgentRole,
} from "@/types/user_agent_setting";

// ─── Static config — the role-agnostic kit fed Studio-specific props ──────────
//
// SettingsPage is now a thin adapter (CR-NS-079): the look + logic live in the
// nex-shared SettingsKit (v0.9.0); this file owns ONLY data/IO (API calls +
// stores) and the Studio capability config. Zero behaviour change vs the old
// 813-line page — this is the vzor proof that the kit extraction is faithful.

// Agenti tab (CR-NS-040): per-role model/effort the cockpit applies at dispatch.
// Labels come from the canonical ROLE_LABELS (labels.ts, CR-NS-018) — single
// source of truth shared with the pipeline board, so role names never drift.
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

// The 5 levels `claude --effort` accepts (no ultracode — see CR-NS-040 policy).
const AGENT_EFFORTS: AgentEffort[] = ["low", "medium", "high", "xhigh", "max"];

// System-settings categories. Every key whose prefix matches one of `prefixes`
// is rendered under the category; keys matching none fall into the trailing
// "Ostatné" bucket the kit panel appends itself.
const SETTINGS_CATEGORIES: SettingsCategory[] = [
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

// Role options drive BOTH the Users filter and the create/edit form. Order
// matters: the kit UserForm defaults to the first option → "shu", matching the
// old form's hardcoded default.
const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "shu", label: "shu — Junior" },
  { value: "ha", label: "ha — Medior" },
  { value: "ri", label: "ri — Director" },
];

// Studio renders the full user field set; password min 5 mirrors the backend
// Pydantic constraint (Director directive 2026-05-13, internal app).
const USER_FIELD_SCHEMA: UserFieldSchema = {
  username: true,
  names: true,
  telegram: true,
  passwordMinLength: 5,
};

const SETTINGS_KIT_CONFIG: SettingsKitConfig = {
  tabs: ["system", "agents", "users", "sessions"],
  labels: {
    system: "Systém",
    agents: "Agenti",
    users: "Používatelia",
    sessions: "Relácie",
  },
  // Sessions is admin/ha tooling — gated ha-or-above to match the backend's
  // `require_ha_or_above` on /api/v1/user-sessions. The other three tabs are
  // visible to every authenticated user (unchanged from the old page).
  tabVisibleForRole: (tab, role) =>
    tab === "sessions" ? role === "ri" || role === "ha" : true,
};

function roleCls(role: string) {
  if (role === "ri") return "text-[var(--color-accent-primary)]";
  if (role === "ha") return "text-[var(--color-status-success)]";
  return "text-[var(--color-status-warning)]";
}

// ─── Per-tab adapters ─────────────────────────────────────────────────────────
//
// SettingsShell mounts only the active tab's panel, so each adapter fires its
// data load on mount → lazy-first-load (matching the old page), while the
// page-level `loaded` guards keep it load-once across tab switches.

function SystemTab({
  settings,
  loaded,
  loadError,
  canEdit,
  onLoad,
  onSave,
}: {
  settings: SystemSettingRead[];
  loaded: boolean;
  loadError: string;
  canEdit: boolean;
  onLoad: () => void;
  onSave: (key: string, value: string) => Promise<SystemSettingRead>;
}) {
  useEffect(() => {
    onLoad();
  }, [onLoad]);
  return (
    <SystemSettingsPanel
      settings={settings}
      categories={SETTINGS_CATEGORIES}
      canEdit={canEdit}
      onSave={onSave}
      loading={!loaded}
      loadError={loadError}
    />
  );
}

function AgentsTab({
  drafts,
  loadError,
  saveErrors,
  onLoad,
  onSave,
}: {
  drafts: Record<string, AgentDraft>;
  loadError: string;
  saveErrors: Record<string, string>;
  onLoad: () => void;
  onSave: (roleId: string, draft: AgentDraft) => Promise<void>;
}) {
  useEffect(() => {
    onLoad();
  }, [onLoad]);
  return (
    <AgentsPanel
      roles={AGENT_ROLES}
      models={AGENT_MODELS}
      efforts={AGENT_EFFORTS}
      drafts={drafts}
      onSave={onSave}
      loadError={loadError}
      saveErrors={saveErrors}
    />
  );
}

function UsersTab({
  users,
  canManage,
  onLoad,
  onCreate,
  onUpdate,
  onDelete,
  onChangePassword,
  onToggleActive,
}: {
  users: UserRead[];
  canManage: boolean;
  onLoad: () => void;
  onCreate: (data: UserFormData) => Promise<void>;
  onUpdate: (id: string, data: UserFormData) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onChangePassword: (id: string, password: string) => Promise<void>;
  onToggleActive: (user: KitUserRead) => Promise<void>;
}) {
  useEffect(() => {
    onLoad();
  }, [onLoad]);
  return (
    <UsersPanel
      users={users}
      roleOptions={ROLE_OPTIONS}
      canManage={canManage}
      fieldSchema={USER_FIELD_SCHEMA}
      onCreate={onCreate}
      onUpdate={onUpdate}
      onDelete={onDelete}
      onChangePassword={onChangePassword}
      onToggleActive={onToggleActive}
      roleClass={roleCls}
    />
  );
}

function SessionsTab({
  sessions,
  loaded,
  loadError,
  canRevoke,
  resolveUsername,
  onLoad,
  onRevoke,
}: {
  sessions: UserSessionRead[];
  loaded: boolean;
  loadError: string;
  canRevoke: boolean;
  resolveUsername: (userId: string) => string;
  onLoad: () => void;
  onRevoke: (id: string) => Promise<void>;
}) {
  useEffect(() => {
    onLoad();
  }, [onLoad]);
  return (
    <SessionsPanel
      sessions={sessions}
      resolveUsername={resolveUsername}
      canRevoke={canRevoke}
      onRevoke={onRevoke}
      loading={!loaded}
      loadError={loadError}
    />
  );
}

// ─── SettingsPage ─────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const role = user?.role ?? "";
  const isRi = role === "ri";
  const isHaOrAbove = role === "ri" || role === "ha";

  // ── System settings ──
  const [settings, setSettings] = useState<SystemSettingRead[]>([]);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState("");

  const loadSettings = useCallback(() => {
    if (settingsLoaded) return;
    listSystemSettingsApi()
      .then((rows) => {
        setSettings(rows);
        setSettingsLoaded(true);
      })
      .catch(() => setSettingsLoadError("Nepodarilo sa načítať nastavenia."));
  }, [settingsLoaded]);

  const handleSaveSetting = useCallback(
    async (key: string, value: string): Promise<SystemSettingRead> => {
      // Returns the stored row so the panel can refresh its draft; we also
      // update the list so the override/timestamp metadata reflects the save.
      const updated = await updateSystemSettingApi(key, value);
      setSettings((prev) => prev.map((s) => (s.key === key ? updated : s)));
      return updated;
    },
    [],
  );

  // ── Agents (per-user model/effort, CR-NS-040) ──
  const [agentDrafts, setAgentDrafts] = useState<Record<string, AgentDraft>>({});
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [agentsLoadError, setAgentsLoadError] = useState("");
  const [agentSaveErrors, setAgentSaveErrors] = useState<Record<string, string>>(
    {},
  );

  const loadAgents = useCallback(() => {
    if (agentsLoaded) return;
    listUserAgentSettingsApi()
      .then((rows) => {
        const initial: Record<string, AgentDraft> = {};
        for (const r of AGENT_ROLES) initial[r.id] = { model: "", effort: "" };
        for (const row of rows) {
          initial[row.agent_role] = {
            model: row.model ?? "",
            effort: row.effort ?? "",
          };
        }
        setAgentDrafts(initial);
        setAgentsLoaded(true);
      })
      .catch(() =>
        setAgentsLoadError("Nepodarilo sa načítať konfiguráciu agentov."),
      );
  }, [agentsLoaded]);

  const handleSaveAgent = useCallback(
    async (roleId: string, draft: AgentDraft): Promise<void> => {
      setAgentSaveErrors((prev) => ({ ...prev, [roleId]: "" }));
      try {
        await upsertUserAgentSettingApi(roleId as PipelineAgentRole, {
          model: (draft.model || null) as AgentModel | null,
          effort: (draft.effort || null) as AgentEffort | null,
        });
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Neznáma chyba.";
        setAgentSaveErrors((prev) => ({ ...prev, [roleId]: msg }));
        throw e; // rethrow so the panel does not flash a false success
      }
    },
    [],
  );

  // ── Users ──
  const [users, setUsers] = useState<UserRead[]>([]);
  const [usersLoaded, setUsersLoaded] = useState(false);

  const loadUsers = useCallback(() => {
    if (usersLoaded) return;
    listUsersApi({ limit: 100 })
      .then((res) => {
        setUsers(res.items);
        setUsersLoaded(true);
      })
      .catch(() => {
        /* matches the old page's silent failure → empty table */
      });
  }, [usersLoaded]);

  const handleCreateUser = useCallback(
    async (data: UserFormData): Promise<void> => {
      const u = await createUserApi({
        username: data.username,
        email: data.email,
        password: data.password,
        role: data.role as UserRole,
        first_name: data.first_name || null,
        last_name: data.last_name || null,
        telegram_chat_id: data.telegram_chat_id || null,
      });
      setUsers((prev) => [u, ...prev]);
    },
    [],
  );

  const handleUpdateUser = useCallback(
    async (id: string, data: UserFormData): Promise<void> => {
      const updated = await updateUserApi(id, {
        first_name: data.first_name || null,
        last_name: data.last_name || null,
        telegram_chat_id: data.telegram_chat_id || null,
        email: data.email,
        role: data.role as UserRole,
        is_active: data.is_active,
      });
      setUsers((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
    },
    [],
  );

  const handleChangePassword = useCallback(
    async (id: string, password: string): Promise<void> => {
      await changePasswordApi(id, password);
    },
    [],
  );

  const handleDeleteUser = useCallback(async (id: string): Promise<void> => {
    // Rejects (e.g. 409 FK conflict) propagate so the panel surfaces the
    // "deaktivovať" hint; the row is only dropped on success.
    await deleteUserApi(id);
    setUsers((prev) => prev.filter((x) => x.id !== id));
  }, []);

  const handleToggleActive = useCallback(
    async (u: KitUserRead): Promise<void> => {
      const updated = await updateUserApi(u.id, { is_active: !u.is_active });
      setUsers((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
    },
    [],
  );

  // ── Sessions (ha-or-above; live /user-sessions, CR-NS-079) ──
  const [sessions, setSessions] = useState<UserSessionRead[]>([]);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sessionsLoadError, setSessionsLoadError] = useState("");

  const loadSessions = useCallback(() => {
    if (sessionsLoaded) return;
    listUserSessionsApi()
      .then((res) => {
        setSessions(res.items);
        setSessionsLoaded(true);
      })
      .catch(() => setSessionsLoadError("Nepodarilo sa načítať relácie."));
  }, [sessionsLoaded]);

  // The Sessions panel resolves user ids → names from the users list, so make
  // sure both are loaded when the tab opens.
  const loadSessionsTab = useCallback(() => {
    loadSessions();
    loadUsers();
  }, [loadSessions, loadUsers]);

  const handleRevokeSession = useCallback(
    async (id: string): Promise<void> => {
      await deleteUserSessionApi(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
    },
    [],
  );

  const usernameById = useMemo(() => {
    const m: Record<string, string> = {};
    for (const u of users) m[u.id] = u.username;
    return m;
  }, [users]);

  const resolveUsername = useCallback(
    (uid: string) => usernameById[uid] ?? uid,
    [usernameById],
  );

  // ── Header badge (role coloring stays app-side; kit is role-agnostic) ──
  const headerRight = user ? (
    <span className="text-xs text-[var(--color-text-muted)]">
      Prihlásený ako{" "}
      <span className="text-[var(--color-text-secondary)] font-medium">
        {user.username}
      </span>
      {" · "}
      <span className={`font-mono text-[11px] ${roleCls(role)}`}>{role}</span>
    </span>
  ) : undefined;

  return (
    <SettingsShell
      config={SETTINGS_KIT_CONFIG}
      currentUserRole={role}
      title="Nastavenia"
      headerRight={headerRight}
      panels={{
        system: (
          <SystemTab
            settings={settings}
            loaded={settingsLoaded}
            loadError={settingsLoadError}
            canEdit={isRi}
            onLoad={loadSettings}
            onSave={handleSaveSetting}
          />
        ),
        agents: (
          <AgentsTab
            drafts={agentDrafts}
            loadError={agentsLoadError}
            saveErrors={agentSaveErrors}
            onLoad={loadAgents}
            onSave={handleSaveAgent}
          />
        ),
        users: (
          // canManage is unconditionally true: the old Users tab had no FE role
          // gate (the backend enforces authz), so to keep zero behaviour change
          // the management UI stays visible to every role.
          <UsersTab
            users={users}
            canManage
            onLoad={loadUsers}
            onCreate={handleCreateUser}
            onUpdate={handleUpdateUser}
            onDelete={handleDeleteUser}
            onChangePassword={handleChangePassword}
            onToggleActive={handleToggleActive}
          />
        ),
        sessions: (
          <SessionsTab
            sessions={sessions}
            loaded={sessionsLoaded}
            loadError={sessionsLoadError}
            canRevoke={isHaOrAbove}
            resolveUsername={resolveUsername}
            onLoad={loadSessionsTab}
            onRevoke={handleRevokeSession}
          />
        ),
      }}
    />
  );
}
