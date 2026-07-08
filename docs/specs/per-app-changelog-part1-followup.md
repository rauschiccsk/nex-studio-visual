# Follow-up — Part 1 correction: released version must stay verified after the graduation note-move

Adversarial verification of Part 1 (auto-write RELEASE_NOTES.md) returned PASS on the three completion
seams, commit scoping, immutability, and serving consistency. ONE real regression: the graduation
note-move commit. Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from root + ruff.

## The defect
`deploy._graduate_version_in_place` → `_move_release_note_dir` (deploy.py ~564-589) commits the moved
`RELEASE_NOTES.md` into the app repo AFTER the version was completed + SHA-anchored. That commit advances
the app-repo HEAD past the anchored `verified_sha`/`hotovo_sha`. `version_verified` (orchestrator.py
~1965-2022) compares the stored SHA to live HEAD and has NO `released` short-circuit → the just-graduated
`v1.0.0` now reads `sha_drift`/`hotovo_drift` (unverified) → it drops out of `list_verified_versions`
(deploy.py ~188-213) and every later deploy of it is hard-blocked (deploy.py ~462-467: "not verified …
re-run Verifikácia"). A `released` version can't be re-verified → soft-locked out of all further deploys
(fatal for instance-per-customer: the 2nd customer deploy + any redeploy of v1.0.0 break).

## Fix — `released` is verified by definition (the robust, general fix)
In `version_verified` (orchestrator.py ~1965-2022), add a short-circuit at the TOP: if the version's
`status == "released"`, return verified `(True, "released")` (or the codebase's verified-tuple shape) —
BEFORE the SHA/HEAD drift comparison. Rationale: a released version is an immutable, already-shipped
record; its verification happened at release. No post-release commit (the note move, or any future
maintenance commit) may un-verify a shipped release. This also closes the general latent gap (the
note-move merely exposed it), not just this one commit.

Do NOT instead try to re-anchor the SHA in graduation — the released short-circuit is smaller and covers
every post-release commit, not just the move.

Guardrails: touch ONLY the verified-status recompute for `released` versions. A not-yet-released version's
drift detection (the real guardrail that catches code changing after a Verifikácia PASS) MUST be
unchanged — only `released` short-circuits.

## Tests (RED→GREEN)
- After graduation (version renamed to v1.0.0, status released, note-move commit advances HEAD),
  `version_verified` for that version returns verified (NOT drift) — this is the exact gap the existing
  graduation test missed (it only checked the file/tree, never called version_verified afterward).
- A NOT-released version whose HEAD moved past its anchored SHA STILL reports drift (unchanged guardrail).
- Full `pytest` from root + ruff.
