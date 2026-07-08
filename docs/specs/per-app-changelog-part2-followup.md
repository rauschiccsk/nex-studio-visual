# Follow-up — Part 2 gate robustness (probe retry + detector brittleness)

Adversarial verification of the Aktualizácie release gate returned: deadlock fix PASS, v-normalization
PASS, gating wiring PASS (verifikacia hard-floor). Two real ROBUSTNESS defects (not logic) — both can
mis-fire on a real build. Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from
root + ruff.

## Fix 1 — 2a probe has no retry → transient/cold-start FALSE BLOCKER
`_probe_release_notes` (orchestrator.py ~4348) execs the fetch ONCE. The release-notes handler reads
many files; its first cold request can exceed the probe's `timeout=10` → no `RELEASE_NOTES_STATUS` line →
`status is None` → `(False, "…neodpovedalo…")` → a good build is falsely blocked. Contrast
`_await_http_ready` (~3954) which retries to a timeout.
Fix: retry the 2a probe a few times with a short sleep before declaring "neodpovedalo" (mirror
`_await_http_ready`'s retry loop / budget). Distinguish "probe couldn't run" (retry, then transient-fail
message) from a real HTTP status (404/500 → immediate blocker, no retry needed). Keep it best-effort +
never-raise.

## Fix 2 — 2b detectors brittle → false-FAIL on valid apps + false-PASS that defeats the gate
`_check_aktualizacie_frontend` (~4244) matches only the exact reference-scaffold shape. Tighten/broaden:
- **Route (false-FAIL):** `_UPDATES_ROUTE_RE` matches only JSX `path="updates"`. ALSO accept the
  data-router object form `path: "updates"` (a `path:` property). Anchor so a stray
  `const filepath = "updates"` does NOT match (require the route/router context, not a bare substring).
- **Page (false-FAIL):** the check is the exact `frontend/src/pages/UpdatesPage.tsx`. Broaden to any
  `frontend/src/**/Updates*.tsx` OR a source that imports an updates page — so a validly-renamed page
  isn't blocked.
- **Nav (false-PASS + false-FAIL):** `/Aktualiz/i` over any non-page file both (a) FALSE-PASSES on an
  unrelated "Naposledy aktualizované" label (a dropped sidebar entry then slips the gate — the worst
  case, it defeats the whole gate) and (b) FALSE-FAILS a valid English "Updates"/"Changelog" label.
  Tighten to a nav-entry CONTEXT (a NavItem/label whose target is the `/updates` route or whose text is
  the changelog label), not a bare accent-stem anywhere. Prefer keying the nav check on the `/updates`
  navigation target (route-anchored) rather than the label text, so it's language-agnostic AND can't
  false-pass on an unrelated "aktualizované" string.

The gate MUST still catch nex-payables (page + route + nav ALL dropped) — keep a test asserting that.

## Tests (RED→GREEN)
- Fix 1: a probe whose first attempt times out but a later attempt returns 200+valid → PASS (not a false
  block); a real 404/500 → immediate blocker (no wasteful retry); a probe that never runs → transient-fail
  message after the retries.
- Fix 2: object-router `path: "updates"` → route detected (no false-fail); `const filepath="updates"` →
  NOT a route; a renamed `pages/Changelog/UpdatesView.tsx` importing the updates page → page detected; a
  dropped sidebar entry with an unrelated "Naposledy aktualizované" elsewhere → nav NOT detected (blocker);
  the full nex-payables state (all three missing) → still blocked.
- Full `pytest` from root + ruff.

## Noted, NOT in scope (pre-existing)
- v3 conversation-flow `hotovo` sign-off checks only `spec_approved`+`kontrola_done`, not the runtime
  floor — so the gate hard-blocks the Auditor/verifikacia path but the conversation path relies on the
  floor note + UI post-filter. This is the known release-oracle gap (smoke doesn't self-gate hotovo),
  tracked separately; the Aktualizácie gate rides the identical rail (no weaker than the existing boot
  floor). Do NOT expand scope to fix the release oracle here.
