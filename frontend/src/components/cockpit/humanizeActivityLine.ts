// Sanitizer for the live agent-activity feed (cockpit-timeout-and-activity-fix.md Bug 2). Kept in its own
// module (not in PipelineActivityFeed.tsx) so the component file exports ONLY the component — the
// react-refresh/only-export-components rule stays happy while the helper is still unit-testable.

// Internal sentinel fences the agent emits as machine markers (`<<<PIPELINE_STATUS>>>`,
// `<<<TASK_PLAN_JSON>>>`, their `<<<END_…>>>` closers). ``activity_line`` (backend) takes the FIRST text
// block truncated to 140 chars, so a leaked marker arrives here as a fragment — never a human line.
const MARKER_RE = /<<<[^>]*>>>/g;

/**
 * Sanitize one live-activity line for display.
 *
 * A line with a leaked internal marker gets the `<<<…>>>` fences stripped and the raw JSON payload that
 * rides with them dropped (machine noise, not a human line) — any human prose that preceded the marker is
 * kept, and a line that collapses to nothing falls back to a neutral human placeholder. A line WITHOUT a
 * marker is returned intact: legit tool lines can contain `{` / `[` (e.g. a pytest `-k "test[1]"` command)
 * and must never be clipped.
 */
export function humanizeActivityLine(raw: string): string {
  if (!raw.includes("<<<")) return raw;
  let s = raw.replace(MARKER_RE, " ");
  // Drop the raw JSON blob that rode with the marker; keep the prose before it.
  const jsonStart = s.search(/[{[]/);
  if (jsonStart !== -1) s = s.slice(0, jsonStart);
  s = s.replace(/\s+/g, " ").trim();
  return s || "pripravuje štruktúrovaný výstup…";
}
