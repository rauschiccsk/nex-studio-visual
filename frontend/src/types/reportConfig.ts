/**
 * TypeScript type definitions for the ``ReportConfig`` domain object.
 *
 * Mirrors ``backend.schemas.report_config`` — stores the per-project
 * senior / junior hourly rates (EUR) used by the reporting pipeline.
 *
 * Rate columns are ``DECIMAL(10, 4)`` on the backend; we represent
 * them as ``string`` on the wire to preserve full precision —
 * JavaScript ``number`` cannot faithfully round-trip arbitrary
 * decimals.
 */

/** Payload for creating a new report configuration. */
export interface ReportConfigCreate {
  /** Project the report configuration belongs to; unique. */
  project_id: string;
  /** Senior developer hourly rate in EUR; server default ``75.0000``. */
  senior_hourly_rate_eur?: string;
  /** Junior developer hourly rate in EUR; server default ``35.0000``. */
  junior_hourly_rate_eur?: string;
}

/**
 * Partial update for an existing report configuration.
 *
 * ``project_id`` is immutable — the row's identity is the project it
 * configures.
 */
export interface ReportConfigUpdate {
  senior_hourly_rate_eur?: string;
  junior_hourly_rate_eur?: string;
}

/** Serialised representation of a report configuration row. */
export interface ReportConfigRead {
  id: string;
  project_id: string;
  /** DECIMAL(10, 4) encoded as a string on the wire. */
  senior_hourly_rate_eur: string;
  /** DECIMAL(10, 4) encoded as a string on the wire. */
  junior_hourly_rate_eur: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
