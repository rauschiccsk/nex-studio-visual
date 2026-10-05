import api from "../api";
import type {
  AgentModelOption,
  PipelineAgentRole,
  UserAgentSettingRead,
  UserAgentSettingUpsert,
} from "../../types/user_agent_setting";

/** The authenticated user's per-role model/effort config rows (only roles they have set). */
export function listUserAgentSettingsApi(): Promise<UserAgentSettingRead[]> {
  return api.get<UserAgentSettingRead[]>("/user-agent-settings");
}

/** The model families on offer, strongest first, each with the version that last really ran (ICCINT-167). */
export function listAgentModelsApi(): Promise<AgentModelOption[]> {
  return api.get<AgentModelOption[]>("/user-agent-settings/models");
}

/** Upsert the caller's model + effort for one pipeline role. */
export function upsertUserAgentSettingApi(
  agentRole: PipelineAgentRole,
  body: UserAgentSettingUpsert,
): Promise<UserAgentSettingRead> {
  return api.put<UserAgentSettingRead>(`/user-agent-settings/${agentRole}`, body);
}
