# obs-2 Part B — per-app Aktualizácie as a guaranteed standard (auto-written notes + release gate)

Director-approved 2026-07-08. Every app NEX Studio generates must reliably ship its OWN working
Aktualizácie (changelog) tab with real per-version notes — as a STANDARD, not a per-project task the
AI agent may drop. Discovery finding: the FE tab is scaffolded + the BE endpoint is charter-mandated,
but in the flagship v3 app (nex-payables) NONE of it landed — the agent built custom UI and dropped
the scaffolded Aktualizácie page, never built the `/api/v1/release-notes` endpoint, and left only the
unfilled placeholder note. So the current mechanism is agent-dependent and fragile. Make it
deterministic (NEX Studio owns it) + enforced (can't ship without it).

Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from repo ROOT + ruff.
Build Part 1 first (independently shippable); if Part 2 grows large/risky, STOP after Part 1 and report.

Grounding (from two Explore maps — cite when locating):
- Serving stack the generated app already aims to have (the pattern): FE `UpdatesPage` +
  `Sidebar` "Aktualizácie" nav + `releaseNotes.ts` client (frontend-skeleton); BE
  `GET /api/v1/release-notes` reads committed `docs/specs/versions/v*/RELEASE_NOTES.md` (charter-mandated,
  reference impl = NEX Studio's own `backend/services/release_notes.py`).
- Note template (mirror its plain-language Slovak, H2-per-version, NO internal codes): scaffolded at
  `docs/specs/versions/v0.1.0/RELEASE_NOTES.md` (from
  `/home/icc/knowledge/templates/claude-project/docs/specs/versions/v0.1.0/RELEASE_NOTES.md.tmpl`).
- Material (control-plane DB): `Epic(version_id, title, plain_description, status)`
  (backend/db/models/tasks.py:19), `Feat(epic_id, …, plain_description)` (:58),
  `Task(feat_id, …, plain_description)` (:92), `Bug(version_id, title, severity, status)`
  (backend/db/models/bugs.py:18). `plain_description` is already jargon-free manager-facing prose.
- Completion seam: `apply_action` `if action == "hotovo":` (orchestrator.py ~7619-7674); `proj_root`
  resolved ~7640, `_vnum` ~7643, `_git_tag_version` ~7644. Phase-automaton verdict-PASS done-branch ~7126.
- Graduation: `deploy._graduate_version_in_place` (backend/services/deploy.py ~561-601) renames the
  built version to `v1.0.0` on first PROD deploy + sets `release_date`.

---

## Part 1 — NEX Studio auto-writes the per-version RELEASE_NOTES.md (deterministic)

At build completion, NEX Studio (NOT the agent) generates the plain-language note for the completing
version and commits it into the generated app's repo, so the app's own `/api/v1/release-notes`
endpoint serves it.

1. **Generator** — new `backend/services/release_note_writer.py` (or a helper in the orchestrator
   service): `write_release_note(db, version_id, proj_root)`:
   - Read the version's **Epics** (by `version_id`, ordered by number) — Epics are the user-facing
     feature level (right altitude; do NOT dump every Task — too granular). For each Epic use its
     `plain_description` (fallback to `title` if null). Optionally append a short "Opravené" line
     summarizing resolved `Bug`s (by `version_id`, status resolved) in plain language (title only, no
     codes/severity jargon).
   - Render Slovak, mirroring the scaffold template: `## <version> — <version name/short summary>`
     then plain-language bullets. NO internal codes (CR-/EPIC-/BUG-/file names) — strip/never emit.
   - Write to `proj_root/docs/specs/versions/v{version_number}/RELEASE_NOTES.md` (mkdir -p). This is
     the SAME layout the serving service globs.
2. **Wire at completion**: call it in the `hotovo` branch right after `_vnum` is read (~7643) and
   **before** `_git_tag_version` (~7644); then `git add docs/specs/versions/v{N}/RELEASE_NOTES.md`
   and commit it into the app repo (so it's in the tagged commit AND baked into the app's backend
   image which COPYs only `RELEASE_NOTES.md`). Mirror the same call into the phase-automaton
   verdict-PASS done-branch (~7126). Use the existing repo helpers (`_repo_head`, the git plumbing
   already used by `_git_tag_version`) — do not shell out ad-hoc.
3. **Determinism + immutability**: NEX Studio is the source of truth — (re)generate the note for the
   completing version at `hotovo` (overwrite a stale placeholder). A version already `released` is
   immutable — do NOT regenerate its note. The agent no longer needs to author it; relax the designer
   charter §9.1 authorship burden to "NEX Studio auto-writes; do not hand-author" (update the charter
   template `/home/icc/knowledge/templates/claude-project/.claude/agents/designer/CLAUDE.md.tmpl` §9.1
   accordingly — one edit, KB change → reindex per §13).
4. **Graduation dir-move**: when `_graduate_version_in_place` renames the built version (e.g.
   `v0.1.0`→`v1.0.0`), MOVE `docs/specs/versions/v0.1.0/RELEASE_NOTES.md` →
   `.../v1.0.0/RELEASE_NOTES.md` (and commit) so the served version number matches the note dir.
   Otherwise the endpoint (which matches by `version_number`) shows no note for the graduated version.

## Part 2 — Hard release gate: the app must actually serve a working Aktualizácie

Make the scaffolded FE tab + charter-mandated BE endpoint NON-OPTIONAL by adding a deterministic
release check NEX Studio runs itself (not the agent):
1. **Behavioural (BE)**: in the release smoke / oracle (`_run_release_smoke` ~orchestrator.py:4040,
   which already boots an ephemeral stack), assert `GET /api/v1/release-notes` returns 200 + a JSON
   list that INCLUDES the completing version's note. Failure = a release blocker.
2. **Static (FE)**: assert the generated app's frontend still has the Aktualizácie route + page + nav
   (e.g. `frontend/src/pages/UpdatesPage.tsx` present AND the `/updates` route wired AND a nav entry) —
   a source-tree check (the agent dropped these in nex-payables). Failure = a release blocker.
3. A build must NOT reach `done`/deploy while either check fails — surface it as an honest blocker in
   the kontrola/release report so the manager sees "Aktualizácie chýba" rather than a silent pass.

---

## Tests (mandatory, RED→GREEN)
- Part 1: given a version with Epics carrying `plain_description`, `write_release_note` produces a
  plain-language Slovak `RELEASE_NOTES.md` (H2 version heading, Epic bullets, no internal codes),
  written at the correct path; `hotovo` commits it; a released version's note is NOT regenerated;
  graduation moves the note dir to the new version number.
- Part 2: the release gate FAILS when `/api/v1/release-notes` is absent/500 or the FE UpdatesPage/route
  is missing, and PASSES when both are present (mirror nex-payables' missing state as the RED case).
- Full `pytest` from root + ruff. FE of NEX Studio itself unaffected (this is generator/pipeline + KB
  template changes).

## Out of scope (note, don't build)
- Backfilling nex-payables' missing tab/endpoint — Dedo handles that separately (it's a deployed app;
  its next version will pick up the standard). Flag, don't auto-fix here.
- A shared-library (nex-shared) BE release-notes package — a cleaner long-term dedup, but larger;
  note as a follow-up, keep this pass to the deterministic auto-write + gate.
