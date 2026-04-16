/**
 * TypeScript type definitions for the Guardian domain objects.
 *
 * Mirrors ``backend.schemas.guardian`` — covers both ``GuardianPrecedent``
 * (allowlist / precedent decisions) and ``GuardianReview`` (the Layer
 * 1 / 2 / 3 review result for a delegation).
 */

/**
 * Mirrors ``verdict IN ('allow', 'notice', 'block')`` on the
 * ``guardian_precedents`` table.
 */
export type GuardianVerdict = "allow" | "notice" | "block";

/**
 * Mirrors ``layer IN ('layer1', 'layer2', 'layer3')`` on the
 * ``guardian_reviews`` table.
 */
export type GuardianReviewLayer = "layer1" | "layer2" | "layer3";

/**
 * Mirrors ``risk_level IN ('low', 'medium', 'high', 'critical')`` on
 * the ``guardian_reviews`` table.
 */
export type GuardianReviewRiskLevel = "low" | "medium" | "high" | "critical";

/**
 * Opaque finding object stored as JSONB — keys include ``severity``,
 * ``rule``, ``file_path``, ``line_range``, ``description``,
 * ``suggestion`` and ``confidence`` but the exact shape is owned by
 * the Guardian pipeline and may evolve over time.  We therefore type
 * each finding as an open ``Record<string, unknown>`` rather than an
 * exact interface.
 */
export type GuardianFinding = Record<string, unknown>;

/** Payload for creating a new Guardian precedent. */
export interface GuardianPrecedentCreate {
  /** SHA-256 hex digest of ``rule:file:message[:50]``. 64 chars exactly. */
  pattern_hash: string;
  /** Human-readable description of the precedent pattern. */
  pattern_description: string;
  /** Guardian action: ``allow`` (pass), ``notice`` (warn), ``block`` (fail). */
  verdict: GuardianVerdict;
  /** User who approved this precedent; ``null`` for system-seeded entries. */
  created_by?: string | null;
}

/**
 * Partial update for an existing Guardian precedent.
 *
 * ``pattern_hash`` is a content-addressed identifier and
 * ``created_by`` is an audit column — both are immutable.
 */
export interface GuardianPrecedentUpdate {
  pattern_description?: string;
  verdict?: GuardianVerdict;
}

/** Serialised representation of a Guardian precedent row. */
export interface GuardianPrecedentRead {
  id: string;
  pattern_hash: string;
  pattern_description: string;
  verdict: GuardianVerdict;
  created_by: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
}

/** Payload for creating a new Guardian review. */
export interface GuardianReviewCreate {
  /** Parent delegation (``ON DELETE CASCADE``). */
  delegation_id: string;
  /** Guardian pipeline layer that produced the review. */
  layer: GuardianReviewLayer;
  /** Maximum risk level of the changed files. */
  risk_level: GuardianReviewRiskLevel;
  /** JSONB array of finding objects; server default ``[]``. */
  findings?: GuardianFinding[];
  /** ``true`` when no blocking issues were found; server default ``false``. */
  passed?: boolean;
  /** Wall-clock execution time of the review in milliseconds. */
  duration_ms?: number | null;
}

/**
 * Partial update for an existing Guardian review.
 *
 * ``delegation_id`` and ``layer`` are immutable — reviews are
 * conceptually immutable, but findings / passed / risk_level may be
 * amended by post-hoc precedent filtering.
 */
export interface GuardianReviewUpdate {
  risk_level?: GuardianReviewRiskLevel;
  findings?: GuardianFinding[];
  passed?: boolean;
  duration_ms?: number | null;
}

/** Serialised representation of a Guardian review row. */
export interface GuardianReviewRead {
  id: string;
  delegation_id: string;
  layer: GuardianReviewLayer;
  risk_level: GuardianReviewRiskLevel;
  findings: GuardianFinding[];
  passed: boolean;
  duration_ms: number | null;
  /** ISO-8601 timestamp. */
  created_at: string;
}
