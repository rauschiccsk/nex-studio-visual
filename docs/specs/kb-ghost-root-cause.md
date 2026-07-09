# KB ghost dirs — root-cause fix (test isolation + delete clears RAG)

Recurring problem (cleaned by hand twice — 2026-06-13 and 2026-07-09): the per-project KB docs dir
`/home/icc/knowledge/projects/<slug>/` accumulates GHOST test-scaffold dirs that pollute the
"Dokumentácia" browser + RAG search. Two independent root causes; fix BOTH. Branch `v2.0.0-dev`.
Self-verify: FULL `.venv/bin/python -m pytest -q` from root + ruff. (Backstop/orphan-scan = out of
scope for this pass, Director deferred.)

Grounding (verified):
- Create-Project integration tests `tests/integration/test_project_creation_flow.py` +
  `tests/integration/test_project_creation_validation.py` run the REAL create flow, which writes
  real KB dirs at `{knowledge_base_path}/projects/<slug>/` (default `/home/icc/knowledge`,
  `backend/config/settings.py:76`) via `KnowledgeBaseWriter` — and do NOT tear those dirs down →
  ghosts on disk (and in RAG). This is the SOURCE of the ghosts.
- Project delete: `delete_project` route (`backend/api/routes/projects.py:664`, KB cleanup at ~line 84)
  calls `kb_writer.delete_project(slug)` (`backend/services/knowledge_base_writer.py:136`) which does
  `shutil.rmtree(target)` — **disk only, NOT RAG**. So even a legit project delete leaves the project's
  RAG (Qdrant) entries behind.
- RAG scheme: docs are indexed into Qdrant tenant `icc` with a KB-relative `source_file` payload
  (e.g. `projects/<slug>/STATUS.md`). Delete pattern = filter points by `source_file` and delete
  (see `scripts/rag_index.py::delete_document`). Backend reaches Qdrant via `QDRANT_URL` (host 9130).

---

## Fix 1 — Create-Project tests must NOT write to the real shared KB

Make the Create-Project integration tests run against an ISOLATED KB (a pytest `tmp_path`), so they
never touch `/home/icc/knowledge/` — even if a test crashes mid-run (isolation beats "clean up after").
- Add a fixture (autouse for these test modules) that points the KB root at a `tmp_path` — override
  `settings.knowledge_base_path` (and/or the `KnowledgeBaseWriter` base the create flow uses;
  `get_knowledge_base_writer` dependency) so every KB write in these tests lands under the temp dir.
- Confirm: after the full test run, `/home/icc/knowledge/projects/` gains NO new dirs (the exact
  regression — grep the ghost names `dup-slug-test`/`bad-repo-proj`/`boundary-*`/`structure-test`/
  `test-auth-project`/etc. must NOT appear under the real KB after the suite).
- If the create flow reads the path in a way a fixture can't cleanly override, fall back to a teardown
  that removes each created slug's KB dir — but PREFER the isolated-temp-KB approach (no real-KB touch).

## Fix 2 — Project delete must also clear the project's RAG entries

`kb_writer.delete_project` (or the `delete_project` route right after it) must delete the project's
Qdrant points, not just the disk folder — so a deleted project leaves no ghost in search either.
- Delete all RAG points in tenant `icc` whose `source_file` is under `projects/<slug>/` (the project's
  KB docs). Capture the file list / do the RAG delete keyed on the slug PREFIX (NOT by enumerating the
  disk — after `rmtree` the files are gone); a path-prefixed match `projects/<slug>/` is collision-safe
  (`projects/port-owner/` does not match `projects/cross-port-owner/...`). Use the backend's existing
  Qdrant access (mirror `scripts/rag_index.py::delete_document`'s filter-and-delete).
- Best-effort + never-raise, matching the existing KB/GitHub/UAT cleanups in `delete_project` (a RAG
  failure must NOT undo the already-committed DB delete; log + continue).
- Keep the disk `rmtree` as-is; add the RAG delete alongside it (order: RAG delete can run before or
  after rmtree since it's keyed on the slug, not disk contents).

---

## Tests (RED→GREEN)
- Fix 1: run the Create-Project integration tests → assert NO new dir appears under the real
  `/home/icc/knowledge/projects/` (the isolation holds); the tests still pass against the temp KB.
- Fix 2: after `delete_project` for a slug that had KB docs indexed, assert the Qdrant `icc` tenant has
  ZERO points with `source_file` under `projects/<slug>/` (mock/stub the Qdrant client in the unit test;
  assert the delete filter targets the slug prefix). Also assert best-effort: a Qdrant failure does not
  raise / does not undo the delete.
- Full `pytest` from root + ruff.

## Out of scope (deferred by Director)
- Orphan-reconciliation backstop (periodic scan removing KB dirs/RAG with no owning project). Not now.
