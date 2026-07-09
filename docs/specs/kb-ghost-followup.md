# Follow-up — KB ghost fix: complete the test-isolation coverage (+ minor efficiency)

Adversarial verification: Fix 2 (production RAG delete) PASS — collision-safe, pagination-complete,
best-effort, right tenant. Fix 1 (test isolation) has a REAL coverage gap that reopens the exact
recurrence vector. Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from root + ruff.

## Fix A — isolation coverage is incomplete (the real defect)
`_isolate_create_project_kb` is opt-in via `pytestmark` on only two modules. But
`tests/integration/test_auth_flow.py::test_login_then_create_project` (~:71-83) creates the slug
**`test-auth-project`** on the create SUCCESS path with NO isolation and NO init.sh dry-run — and
`test-auth-project` is EXACTLY one of the ghost names this fix must keep out of the real KB
(`docs/specs/kb-ghost-root-cause.md` acceptance list; a hand-cleaned `test-auth-project` ghost really
existed). So the ghost recurs from this test whenever `template_init_script_path` is configured. Other
create-touching tests OUTSIDE `tests/integration/` are also uncovered (can't see that conftest):
`tests/test_project_router.py`, `tests/api/test_project_create_validation.py`, `tests/api/test_project_ports.py`.

Fix: make the isolation cover EVERY test that can create a project, without breaking tests that
legitimately read the real KB. Recommended:
- Make `_isolate_create_project_kb` **`autouse=True` in `tests/integration/conftest.py`** → covers
  `test_auth_flow.py` (the smoking gun) + every integration create path + a suite-wide sentinel there.
- Extend the SAME isolation to the create-touching modules outside `tests/integration/`
  (`tests/test_project_router.py`, `tests/api/test_project_create_validation.py`,
  `tests/api/test_project_ports.py`) — a shared fixture reachable by those (e.g. an autouse fixture in
  `tests/api/conftest.py` and applied to `test_project_router.py`), OR promote the fixture to a conftest
  those modules inherit. CAUTION: do NOT make it autouse for the ENTIRE suite if that would break tests
  that legitimately read the real `/home/icc/knowledge` (e.g. KB/RAG read tests) — scope it to the
  create-touching tests. The Implementer picks the cleanest scoping that covers all create paths without
  breaking KB-reading tests.
- Neutralize all three vectors in every covered path (settings.knowledge_base_path → tmp,
  get_knowledge_base_writer DI → tmp, invoke_init_script → dry-run), same as the existing fixture.

**Acceptance (the real test):** after the FULL suite run, `/home/icc/knowledge/projects/` gains NO new
dir — SPECIFICALLY assert `test-auth-project` (and the other ghost names) are absent. This is the exact
regression that recurred; make it a first-class assertion.

Optional hardening (nice-to-have, not required): the sentinel compares only TOP-LEVEL dir names, so a
file written inside an existing project dir wouldn't be caught. If cheap, snapshot recursively; else note it.

## Fix B — minor efficiency in the RAG delete
`RAGIndexer.delete_project_documents` (`backend/rag/indexer.py` ~:273) scrolls with `with_payload=True`,
loading the full `content` chunk text of EVERY `projects`-category point across ALL projects just to read
`source_file`. Change to `with_payload=["source_file"]` (mirror `reader.py:207`). Correctness unchanged;
avoids loading all KB content on every project delete.

## Tests (RED→GREEN)
- Fix A: a test exercising the `test_auth_flow` create path (and the router/api create paths) leaves NO
  real-KB dir; the suite-wide sentinel catches any leak. Assert `test-auth-project` absent from the real
  KB after the run (RED before the coverage fix, GREEN after).
- Fix B: `delete_project_documents` requests only the `source_file` payload (assert the scroll call args);
  behaviour unchanged (still deletes exactly the slug-prefixed points).
- Full `pytest` from root + ruff.

## Out of scope
- category-narrowing robustness (a legacy point missing the `category` field would be under-selected) —
  low risk (all in-codebase writes set it); leave as-is. Orphan-scan backstop still deferred.
