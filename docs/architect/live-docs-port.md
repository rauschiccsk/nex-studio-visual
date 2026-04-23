# Live Documents Port — Discovery & Decisions

Port of automated project documentation (`STATUS.md` / `HISTORY.md` / `ARCHITECT.md`) from NEX Command (`backend/services/live_documents.py`) into NEX Studio, adapted to the `Project → Version → Epic → Feat → Task` hierarchy.

This document is the source of truth for the port — technical decisions, ORM mapping, entry points, and open items — and is updated iteratively during implementation.

---

## 1. ORM Mapping — NEX Command → NEX Studio

| NEX Command (source)                       | NEX Studio (target)                                        | Notes |
|--------------------------------------------|------------------------------------------------------------|-------|
| `Project` (slug, name)                     | `Project` (slug, name)                                     | 1:1. |
| `TaskPlan` (per project)                   | *none*                                                     | NEX Studio has no TaskPlan layer. Generator queries directly through `Version → Epic → Feat`. |
| `Feat` (flat, `feat_number`)               | `Epic → Feat`                                              | Numbering adapts: NEX Studio uses hierarchical `{epic.number}.{feat.number}.{task.number}`. |
| `TaskPlanItem` (sibling `sort_order`)      | `Task` (`number` column, monotonic per feat)               | 1:1 conceptually. `TaskCompletionData.task_number` maps to `Task.number`. |
| `ExecutionLog.commit_hashes: list[str]`    | `ExecutionLog.commit_hash: Optional[str]` (single, ≤40 ch) | Schema downgrade: single commit per log row. Our `TaskCompletionData` keeps a list for API compatibility; generators use the first. |
| `ExecutionLog.is_feat_event: bool`         | `ExecutionLog.task_id IS NULL`                             | Feat-level delegation events have no `task_id`. |
| `ExecutionLog.changed_files: list[str]`    | *not present*                                              | NEX Studio does not record changed files. `ARCHITECT.md` entries emit only `### Task X.Y: Title` + `Commits: ...` until a git-diff integration is added. No `Files:` line. |

### `STATUS.md` hierarchy

NEX Command rendered a flat `## Feat N: Title → - [x] N.M Task`. NEX Studio renders hierarchically:

```
# {project.name} — Status
Updated: YYYY-MM-DD HH:MM UTC

## Epic E: {epic.title} — STATUS
### Feat E.F: {feat.title} — STATUS
- [x] E.F.T {task.title} ({commit7})
- [ ] E.F.T+1 ...
```

Versions are optional (`version_id` is nullable in `epics`). When present, versions group epics under a `# Version: v0.1 — {title}` top-level header; when absent, epics render directly under the project header.

---

## 2. Task / Feat Completion Entry Points

| Hook                     | File                                    | Location / trigger                             |
|--------------------------|-----------------------------------------|------------------------------------------------|
| Project creation         | `backend/services/project.py::create`   | After `db.flush()`, before router `commit`. Failure → `raise ValueError` → router rolls back (no project without live docs). |
| Task completion          | `backend/services/task.py::update`      | When incoming `data.status == "done"` AND the prior `task.status != "done"`. Called before `db.flush()` return. |
| Feat completion          | `backend/services/feat.py::update`      | When incoming `data.status == "done"` AND the prior `feat.status != "done"`. Appends `generate_phase_summary_entry`. |

**Rationale for explicit service-layer calls (not SQLAlchemy event listeners):** NEX Studio's service layer is explicit and transaction-agnostic — implicit `after_update` listeners would surprise readers and hide the KB side effect. An explicit call sits next to the status change where it is discoverable and testable.

**Transaction semantics:** hooks run between `flush()` and `commit()`. KB I/O failure → `ValueError` → router rolls back DB. The project/task/feat and the 3 live docs are atomic from the user's perspective.

---

## 3. Permissions — Docker KB Mount

**Current (problem):** `/home/icc/knowledge:/home/icc/knowledge:ro` — read-only.

**Required change:** `/home/icc/knowledge:/home/icc/knowledge:rw`. Container user is `andros` (host uid 1000 = host `andros`), so files written from the backend end up owned by `andros:andros`, consistent with the rest of the KB tree.

**Risk mitigation in `KnowledgeBaseWriter`:**
- Strict `category` allow-list: writes are allowed only under `projects/<slug>/` (root category validated against DB `projects.slug`).
- Filename allow-list: fixed set `{STATUS.md, ARCHITECT.md, HISTORY.md}` initially; regex-guarded `^[a-zA-Z0-9][a-zA-Z0-9._-]*\.md$` for future extensions.
- Path-traversal guard: resolved path must be a descendant of `KNOWLEDGE_BASE_PATH`.
- Blocked top-level prefixes (mirrors NEX Command): `credentials/`, `.git/`, hidden dirs — never writable.

---

## 4. RAG Reindex Policy

Backend writes live documents via Python. The Claude Code `PostToolUse` hook triggers only on `Edit|Write|MultiEdit` from CC tools, so Python writes **do not** trigger auto-reindex.

**Phase 1 (this port):** live documents are **not** indexed into Qdrant. `STATUS.md` and `HISTORY.md` update at per-task frequency and are chronological/structural — semantic search over them adds little and would thrash the index. `ARCHITECT.md` is the only candidate worth indexing long-term.

**Phase 2 (deferred, separate task):** explicit post-milestone reindex call in `LiveDocumentService` when a Feat or Version completes — reindexing only `ARCHITECT.md`. Not in scope for this port.

---

## 5. `KnowledgeBaseWriter` — Interface (preview)

```python
class KnowledgeBaseWriter:
    def __init__(self, base_path: Path): ...

    def save(self, project_slug: str, filename: str, content: str) -> Path: ...
    def read(self, project_slug: str, filename: str) -> str: ...  # FileNotFoundError if missing
    def append(self, project_slug: str, filename: str, entry: str, *, header_if_new: str = "") -> Path: ...
    def exists(self, project_slug: str, filename: str) -> bool: ...
```

- Atomic writes via temp-file + `os.replace` to avoid half-written files during concurrent updates.
- `append` with dedup guard: skip if first line of `entry` already appears in the existing file (matches NEX Command behaviour).
- Tests use `tmp_path` fixture exclusively — no reference to `/home/icc/knowledge` from the test suite.

---

## 6. NEX Command Investigation — Source of April 13 Pollution

- **andros crontab:** `*/5 * * * * duckdns` + `0 3 * * * daily-db-backup.sh` — backup script does `pg_dump` + `tar` of KB; does **not** run pytest.
- **root crontab / systemd timers:** empty / none for NEX / NEX Studio.
- **GitHub Actions:** `ci.yml` + `deploy.yml` only — no `schedule:` trigger.
- **Container entrypoint:** `uvicorn backend.main:app` — not pytest.
- **NEX Command container KB mount:** `/home/icc/knowledge:/app/knowledge:rw` (default mode = rw) — this is how a pytest run **inside the container** could land garbage on the host KB.
- **File owner `root:root`:** inconsistent with container user `andros` → tests were run on the host (likely `sudo pytest` or `docker exec -u root ...`) during a manual GAP session, not on a schedule.

**Conclusion:** no cyclic trigger. Garbage was a one-off on-demand pytest run. It has not reproduced in 10 days. Risk of repeat: any future `pytest tests/test_live_documents.py` or `test_phase_pipeline.py` under a writable KB mount.

**Prevention taken in this port (Step 2 of the plan):**
- Add `@pytest.mark.skip(reason="NEX Command is being retired — LiveDocuments is ported to NEX Studio; skip to prevent real-KB pollution")` to NEX Command test modules that instantiate `LiveDocumentService` with a project slug and no mocked KB writer. This is bug prevention, not functionality investment in a temporary prototype.

---

## 7. Open Items (resolved during implementation)

- [ ] Decide final filename allow-list — start with `{STATUS.md, ARCHITECT.md, HISTORY.md}` only; revisit if other live docs appear.
- [ ] Confirm bind-mount change in `docker-compose.yml` is safe for existing containers (requires `docker compose up -d` to remount).
- [ ] Schema for `TaskCompletionData` — adapt to NEX Studio `Task` fields + join on `ExecutionLog` for commit + duration.
