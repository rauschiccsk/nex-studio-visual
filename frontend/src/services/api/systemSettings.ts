import api from "../api";
import type { SystemSettingRead } from "../../types/system_setting";

/** GET /api/v1/system-settings — list every known setting. */
export function listSystemSettingsApi(): Promise<SystemSettingRead[]> {
  return api.get<SystemSettingRead[]>("/system-settings");
}

/** GET /api/v1/system-settings/{key} — single setting or default. */
export function getSystemSettingApi(key: string): Promise<SystemSettingRead> {
  return api.get<SystemSettingRead>(`/system-settings/${key}`);
}

/**
 * PATCH /api/v1/system-settings/{key} — upsert the value.
 *
 * Requires ri role on the backend (403 otherwise). Returns the stored
 * row with ``is_default=false``.
 */
export function updateSystemSettingApi(
  key: string,
  value: string,
): Promise<SystemSettingRead> {
  return api.patch<SystemSettingRead>(`/system-settings/${key}`, { value });
}
