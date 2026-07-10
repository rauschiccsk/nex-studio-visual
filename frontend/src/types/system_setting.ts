/**
 * TypeScript type definitions for ICC-wide system settings.
 *
 * Mirrors ``backend.schemas.system_setting`` — defaults live in the
 * service layer, a stored override sets ``is_default`` to false.
 */

/** Runtime type of ``value`` — mirrors ``ck_system_settings_value_type``. */
export type SystemSettingValueType = "string" | "int" | "float" | "bool";

export interface SystemSettingRead {
  key: string;
  value: string;
  value_type: SystemSettingValueType;
  description: string | null;
  /** Human display name (card title); the raw ``key`` drops to small info. ``null``/absent → key is title. */
  label?: string | null;
  /** Unit shown after the editor (e.g. "sekúnd"). ``null``/absent → no suffix. */
  unit?: string | null;
  /** ISO-8601 timestamp of last edit; ``null`` when the value is a default. */
  updated_at: string | null;
  /** UUID of the user who last edited; ``null`` for defaults. */
  updated_by: string | null;
  /** Username of the user who last edited; ``null`` for defaults or when the user was deleted. */
  updated_by_username: string | null;
  /** ``true`` when this value comes from the service-layer default. */
  is_default: boolean;
}

export interface SystemSettingUpdate {
  value: string;
}
