# Follow-up — corrections from adversarial verification of the consult sidecar

Adversarial verification of the sidecar (argv contract, host-path, fallback, leak, build-isolation)
returned PASS on 4 of 5 points; the read-only guarantee, honest fallback, and build-turn isolation
are CONFIRMED correct — leave them. One real defect + two minor gaps below. Branch `v2.0.0-dev`.
Self-verify: FULL `.venv/bin/python -m pytest -q` from repo ROOT + ruff.

## Fix 1 — SLUG TRAVERSAL broadens the read-only mount (the real defect)

`build_sidecar_argv` / `_host_project_path` (consult_sandbox.py:115-122, 150) compose the `-v` bind
SOURCE from `project_slug` with NO validation. `pathlib` does not normalize `..`, so:
- slug `..` → source `/opt/projects-v3/..` → docker mounts **`/opt`** `:ro` (all customers/uat/infra/
  projects) into the sidecar;
- slug `../../etc` → `/etc`; slug `../other` → `/opt/other`.
Read-only (no write), but it LEAKS every other project/customer/UAT/infra source into the consult —
defeating the negative half of the guarantee. No upstream slug validation exists (schema is
length-only; `project_service.create` has no regex). The existing test masks it (the forbidden
literals don't appear in the argv when the slug is `..`).

Fix: validate the slug at the mount-composition boundary. Reuse the existing slug validator if there
is one (there is a `_validate_slug` / `_SLUG_RE` in `backend/services/project_specs.py:63` — reuse or
mirror it for DRY); else add a strict `^[a-z0-9][a-z0-9-]*$` check (reject empty, `..`, `/`, anything
non-slug). On a bad slug, raise `SidecarUnavailable` (BEFORE composing any `-v`). Belt-and-suspenders:
after translation, assert the resolved host source path stays under the intended prefix
(`/opt/projects-v3/<slug>` or `/opt/customers/<slug>`) via `os.path.realpath` containment — so even a
future prefix change can't silently broaden the mount.

Test (RED→GREEN): a `..` (and `../other`, `/`, empty) slug must RAISE, and MUST NOT compose a `-v`
source that resolves outside `/opt/projects-v3/<slug>` (assert on the realpath containment, not just
the literal-string absence the current test uses).

## Fix 2 — tmpfs scratch is referenced by nothing (verify via live smoke, then wire if needed)

`CLAUDE_CONFIG_DIR` points at the `:ro` `~/.claude`; the writable `--tmpfs /home/andros/.claude-scratch`
is referenced by no env/HOME. If `claude` needs to WRITE session state under `CLAUDE_CONFIG_DIR`, the
sidecar would fail and systematically degrade to in-process (silently weakening the guarantee to the
deny-list). Dedo's live acceptance (below) settles whether claude runs cleanly with a read-only config
dir. IF the live smoke shows claude needs a writable state dir: wire the tmpfs as the writable state
location (e.g. point the state/history at `/home/andros/.claude-scratch`, keeping the OAuth token read
from the `:ro` mount) so a consult never depends on writing the real `~/.claude`. IF the live smoke is
clean (claude tolerates the `:ro` config dir), drop the now-unused `--tmpfs` line and note why. Do not
guess — let the live result decide; report which branch was taken.

## Fix 3 — no `finally`-reap for unexpected exceptions (minor leak hardening)

The `communicate` block (consult_sandbox.py:266-290) reaps the container only on `TimeoutError` and
`CancelledError`. An unexpected exception type would leave the container until claude self-exits (then
`--rm` reaps). Add a guarded `finally: await _reap_container(container_name)` (or a try/except around
the whole run) so the container is reaped on ANY error path, without double-reaping the clean exit.

## Tests
- Fix 1 as above (traversal RAISES + realpath containment) — the highest-value test.
- Fix 3: an unexpected error mid-run still reaps (mock the subprocess to raise; assert `_reap_container`
  called).
- Full `pytest` from root + ruff. FE unaffected.
