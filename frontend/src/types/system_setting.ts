/**
 * TypeScript type definitions for ICC-wide system settings.
 *
 * Mirrors ``backend.schemas.system_setting`` — defaults live in the
 * service layer, a stored override sets ``is_default`` to false.
 */

export interface SystemSettingRead {
  key: string;
  value: string;
  description: string | null;
  /** ISO-8601 timestamp of last edit; ``null`` when the value is a default. */
  updated_at: string | null;
  /** UUID of the user who last edited; ``null`` for defaults. */
  updated_by: string | null;
  /** ``true`` when this value comes from the service-layer default. */
  is_default: boolean;
}

export interface SystemSettingUpdate {
  value: string;
}
