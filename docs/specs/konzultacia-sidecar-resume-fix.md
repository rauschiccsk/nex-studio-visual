# Fix — consult sidecar EROFS on --resume (auth dir must be writable)

LIVE BUG (Director hit it in v3 2026-07-08): a real consult failed with
`claude invocation failed: consult sidecar claude exited with code 1 … Failed to resume session:
EROFS: read-only file system, open`. Branch `v2.0.0-dev`. Self-verify: FULL
`.venv/bin/python -m pytest -q` from repo ROOT + ruff. Then Dedo rebuilds/redeploys/live-verifies.

## Root cause
The real consult path runs `claude --resume <build-session-uuid>` (the consult resumes the done
version's existing session), which WRITES session state into `CLAUDE_CONFIG_DIR` = the mounted
`~/.claude`. The sidecar mounts `~/.claude` `:ro` → the write is kernel-refused (EROFS) → the turn
fails. The acceptance smoke MISSED this because it used session-less `claude -p` (no `--resume`),
which does not write session state.

Proven fix (Dedo, live): mount `~/.claude` READ-WRITE → create+`--resume` both succeed, no EROFS,
and the PROJECT bind stays `:ro` so a raw-shell write into the project is still kernel-refused. The
project read-only guarantee — the actual guarantee — is UNCHANGED; only claude's own config/session
dir becomes writable (exactly what the in-container build turns already do today).

## Change — `backend/services/consult_sandbox.py` `build_sidecar_argv`
- Auth mount: drop `:ro` → `-v /home/andros/.claude:/home/andros/.claude` (READ-WRITE). Claude
  persists + resumes its own session here.
- Remove the now-dead `--tmpfs /home/andros/.claude-scratch` (it existed only for the `:ro` scenario;
  with a writable `~/.claude` claude writes its session there directly). Keep
  `-e CLAUDE_CONFIG_DIR=/home/andros/.claude`. Drop the `_CLAUDE_SCRATCH_DIR` constant if unused.
- **The project bind stays `:ro`** (unchanged — the guarantee). Everything else unchanged: `--user
  andros`, NO docker.sock / customers / uat / credentials / infra / knowledge, slug validation +
  realpath containment.
- Update the module + function docstrings that currently claim "auth `:ro`" to state accurately: the
  PROJECT is `:ro` (kernel-enforced guarantee); the auth/config dir is writable so claude can
  persist/resume its own session (as the build turns do), which does NOT let the AI touch the project
  (no write tools + kernel `:ro` project).

## Tests (RED→GREEN)
- Update the argv-contract test(s): the PROJECT bind MUST still carry `:ro`; the `~/.claude` bind MUST
  NOT carry `:ro` (writable). Assert `--tmpfs` for the scratch dir is gone. Still assert NO
  docker.sock/customers/uat/credentials/infra mounts.
- Add a test named for the regression (e.g. `test_auth_dir_writable_so_resume_can_persist`) asserting
  the composed auth bind is writable (no `:ro` suffix) — this is the exact gap the live bug exposed.
- Full `pytest` from root + ruff. FE unaffected.
