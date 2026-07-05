"""End-to-end migration integration test (STEP 8) — synthetic v1 → v2, two DBs.

Builds a v1-SHAPED source DB and a v2-head target DB (both throwaway, on the test
server :9178, both names distinct from PROD so the guards pass) and drives the real
tool. Proves: dry-run rolls back, apply preserves every row field-by-field
(including project_members), module_id is dropped, the pipeline delta is 0 (OQ-6),
the deploy_events seq high-water-mark advances, idempotent re-run skips, slug
conflict skips, per-project isolation on failure, credential WARN vs OK, and the
FULL referenced-user pre-flight fails closed with the target untouched.

SYNTHETIC ONLY — never the real v1 source, never PROD (:9198).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.base import Base
from backend.db.models.deploy import DeployEvent
from backend.db.session import _ensure_pg8000_driver
from backend.services.migration import MigrationConfig, run_migration
from backend.services.migration.runner import MigrationPreflightError
from tests.integration.fixtures.synthetic_v1 import (
    ALL_USER_IDS,
    ALPHA,
    BETA,
    GAMMA,
    U_CREATOR,
    U_OWNER,
    bring_to_v2_head,
    create_database,
    create_v1_schema,
    drop_database,
    seed_source,
    seed_target_users,
    truncate_target,
)


def _url_with_db(name: str) -> str:
    base, _ = settings.test_database_url.rsplit("/", 1)
    return _ensure_pg8000_driver(f"{base}/{name}")


SOURCE_URL = _url_with_db("nexstudio_mig_src")
TARGET_URL = _url_with_db("nexstudio_mig_tgt")


class TargetHandle:
    def __init__(self, url, engine):
        self.url = url
        self.engine = engine


# ---------------------------------------------------------------------------
# Fixtures — throwaway source (v1-shaped) + target (v2 head)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _cred_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("mig_creds")
    present = d / "alpha_secret.md"
    present.write_text("SECRET_TOKEN=must-never-be-read", encoding="utf-8")
    missing = d / "beta_secret_absent.md"  # deliberately NOT created
    return str(present), str(missing)


@pytest.fixture(scope="module")
def source_url(_cred_files):
    present, missing = _cred_files
    create_database(SOURCE_URL)
    engine = create_engine(SOURCE_URL)
    create_v1_schema(engine)
    seed_source(engine, present, missing)
    engine.dispose()
    yield SOURCE_URL
    drop_database(SOURCE_URL)


@pytest.fixture(scope="module")
def _target_ready():
    create_database(TARGET_URL)
    bring_to_v2_head(TARGET_URL)
    engine = create_engine(TARGET_URL)
    yield engine
    engine.dispose()
    drop_database(TARGET_URL)


@pytest.fixture()
def target(_target_ready):
    truncate_target(_target_ready)
    seed_target_users(_target_ready)
    return TargetHandle(TARGET_URL, _target_ready)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(source_url, target, *, dry_run, only_slugs=(), report_path=None):
    return MigrationConfig(
        source_url=source_url,
        target_url=target.url,
        dry_run=dry_run,
        only_slugs=only_slugs,
        report_path=report_path,
    )


def _scalar(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _project_row(engine, slug):
    with engine.connect() as c:
        return c.execute(text("SELECT * FROM projects WHERE slug=:s"), {"s": slug}).mappings().first()


def _pid(engine, slug):
    return _scalar(engine, "SELECT id FROM projects WHERE slug=:s", s=slug)


# ---------------------------------------------------------------------------
# 5. DRY-RUN changes nothing
# ---------------------------------------------------------------------------


def test_dry_run_changes_nothing(source_url, target, tmp_path):
    before = _scalar(target.engine, "SELECT COUNT(*) FROM projects")
    report = run_migration(
        _cfg(source_url, target, dry_run=True, only_slugs=(ALPHA,), report_path=str(tmp_path / "r.json"))
    )
    after = _scalar(target.engine, "SELECT COUNT(*) FROM projects")
    assert before == after == 0  # rollback proven — target untouched

    (res,) = report.projects
    assert res.slug == ALPHA
    assert res.status == "dry_run"
    assert res.counts["versions"] == 2
    assert res.counts["project_members"] == 1
    assert res.counts["deploy_events"] == 2
    assert [f for f in res.findings if f["severity"] == "CRITICAL"] == []
    assert report.overall == "ok"
    # The report was written and carries no secret material.
    assert report.report_path is not None
    written = (tmp_path / "r.json").read_text(encoding="utf-8")
    assert "must-never-be-read" not in written


# ---------------------------------------------------------------------------
# 6 + 7. APPLY — per-table parity, PK + timestamps preserved, module_id dropped,
#        credential FK intact, field-by-field deep compare
# ---------------------------------------------------------------------------


def test_apply_parity_and_no_data_loss(source_url, target):
    report = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    assert report.projects[0].status == "migrated"

    src = create_engine(SOURCE_URL)
    tgt = target.engine
    try:
        pid = _pid(tgt, ALPHA)
        assert _pid(src, ALPHA) == pid  # PK UUID preserved

        # per-table count parity (incl. project_members — B1)
        for tbl in ("versions", "epics", "bugs", "backlog_items", "deploy_events", "project_members", "customers"):
            s = _scalar(src, f"SELECT COUNT(*) FROM {tbl} WHERE project_id=:p", p=pid)  # noqa: S608 fixed names
            t = _scalar(tgt, f"SELECT COUNT(*) FROM {tbl} WHERE project_id=:p", p=pid)  # noqa: S608
            assert s == t, f"{tbl} parity"
        # feats/tasks via join
        s_feats = _scalar(
            src, "SELECT COUNT(*) FROM feats f JOIN epics e ON f.epic_id=e.id WHERE e.project_id=:p", p=pid
        )
        t_feats = _scalar(
            tgt, "SELECT COUNT(*) FROM feats f JOIN epics e ON f.epic_id=e.id WHERE e.project_id=:p", p=pid
        )
        assert s_feats == t_feats == 2

        # transforms: multimodule → standard, auth_mode backfilled, source_path rebased, owner preserved
        row = _project_row(tgt, ALPHA)
        assert row["type"] == "standard"
        assert row["auth_mode"] == "password"
        assert row["source_path"] == "/opt/projects-v2/alpha"
        assert row["owner_id"] == U_OWNER

        # module_id is dropped in v2 — the target epics table has no such column at all
        cols = [
            r[0]
            for r in tgt.connect().execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='epics'")
            )
        ]
        assert "module_id" not in cols

        # credential FK intact + registry row copied
        cred_id = _scalar(tgt, "SELECT credential_id FROM customers WHERE project_id=:p", p=pid)
        assert cred_id is not None
        assert _scalar(tgt, "SELECT COUNT(*) FROM credentials WHERE id=:c", c=cred_id) == 1

        # field-by-field: created_at/updated_at preserved verbatim on a version + a task
        version_sql = (
            "SELECT id, version_number, created_at, updated_at "
            "FROM versions WHERE project_id=:p ORDER BY version_number"
        )
        with src.connect() as sc, tgt.connect() as tc:
            sv = (
                sc.execute(
                    text(version_sql),
                    {"p": pid},
                )
                .mappings()
                .all()
            )
            tv = (
                tc.execute(
                    text(version_sql),
                    {"p": pid},
                )
                .mappings()
                .all()
            )
            assert [dict(r) for r in sv] == [dict(r) for r in tv]

            task_sql = (
                "SELECT t.id, t.title, t.description, t.task_type, t.created_at "
                "FROM tasks t JOIN feats f ON t.feat_id=f.id JOIN epics e ON f.epic_id=e.id "
                "WHERE e.project_id=:p ORDER BY t.title"
            )
            st = sc.execute(text(task_sql), {"p": pid}).mappings().all()
            tt = tc.execute(text(task_sql), {"p": pid}).mappings().all()
            assert [dict(r) for r in st] == [dict(r) for r in tt]
    finally:
        src.dispose()


# ---------------------------------------------------------------------------
# 8. PIPELINE DELTA == 0 even with a pre-seeded forward v2 build (OQ-6)
# ---------------------------------------------------------------------------


def _seed_forward_pipeline_row(engine):
    """Seed an unrelated forward v2 build (project+version+pipeline_state) in target."""
    projects = Base.metadata.tables["projects"]
    versions = Base.metadata.tables["versions"]
    pipeline_state = Base.metadata.tables["pipeline_state"]
    with engine.begin() as c:
        pid = uuid.uuid4()
        vid = uuid.uuid4()
        c.execute(
            insert(projects).values(
                id=pid,
                name="Forward Build",
                slug="fwd-build",
                type="standard",
                auth_mode="password",
                description="forward",
                created_by=U_CREATOR,
            )
        )
        c.execute(insert(versions).values(id=vid, project_id=pid, version_number="v9.0.0", status="active"))
        c.execute(
            insert(pipeline_state).values(
                version_id=vid,
                flow_type="new_version",
                current_stage="programovanie",
                current_actor="ai_agent",
                status="agent_working",
            )
        )


def test_pipeline_delta_zero_with_preseeded_forward_row(source_url, target):
    _seed_forward_pipeline_row(target.engine)
    before = _scalar(target.engine, "SELECT COUNT(*) FROM pipeline_state")
    assert before == 1

    report = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    assert report.projects[0].status == "migrated"

    after = _scalar(target.engine, "SELECT COUNT(*) FROM pipeline_state")
    assert after == before  # delta 0 — the migration inserts ZERO pipeline rows (OQ-6)
    assert _scalar(target.engine, "SELECT COUNT(*) FROM pipeline_message") == 0


# ---------------------------------------------------------------------------
# 9. DEPLOY seq high-water-mark advanced (M3)
# ---------------------------------------------------------------------------


def test_deploy_seq_high_water_mark(source_url, target):
    run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    pid = _pid(target.engine, ALPHA)
    max_migrated = _scalar(target.engine, "SELECT MAX(seq) FROM deploy_events")
    assert max_migrated == 101
    cust_id = _scalar(target.engine, "SELECT id FROM customers WHERE project_id=:p", p=pid)

    # A fresh post-migration deploy_event (ORM → IDENTITY assigns the next seq) must not
    # collide and must order AFTER every migrated row (the UAT-acceptance recency query).
    with Session(target.engine) as s:
        ev = DeployEvent(
            customer_id=cust_id,
            project_id=pid,
            version_number="v1.1.0",
            environment="prod",
            event_type="deploy",
            status="ok",
        )
        s.add(ev)
        s.commit()
        s.refresh(ev)
        new_seq = ev.seq
    assert new_seq > max_migrated


# ---------------------------------------------------------------------------
# 10. IDEMPOTENT re-run skips
# ---------------------------------------------------------------------------


def test_idempotent_rerun_skips(source_url, target):
    r1 = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    assert r1.projects[0].status == "migrated"
    projects_after_1 = _scalar(target.engine, "SELECT COUNT(*) FROM projects")
    versions_after_1 = _scalar(target.engine, "SELECT COUNT(*) FROM versions")

    r2 = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    assert r2.projects[0].status == "skipped"
    assert r2.exit_code == 0
    # No duplicates, counts unchanged.
    assert _scalar(target.engine, "SELECT COUNT(*) FROM projects") == projects_after_1
    assert _scalar(target.engine, "SELECT COUNT(*) FROM versions") == versions_after_1


# ---------------------------------------------------------------------------
# 11. SLUG CONFLICT skips (no overwrite of operator edits)
# ---------------------------------------------------------------------------


def test_slug_conflict_skips_no_overwrite(source_url, target):
    projects = Base.metadata.tables["projects"]
    with target.engine.begin() as c:
        c.execute(
            insert(projects).values(
                name="Operator Alpha",
                slug=ALPHA,
                type="standard",
                auth_mode="password",
                description="operator edit — keep me",
                created_by=U_CREATOR,
            )
        )

    dry = run_migration(_cfg(source_url, target, dry_run=True, only_slugs=(ALPHA,)))
    assert dry.projects[0].status == "skipped"

    app = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    assert app.projects[0].status == "skipped"

    row = _project_row(target.engine, ALPHA)
    assert row["description"] == "operator edit — keep me"  # never overwritten (no UPSERT)
    assert _scalar(target.engine, "SELECT COUNT(*) FROM projects WHERE slug=:s", s=ALPHA) == 1


# ---------------------------------------------------------------------------
# 13. PER-PROJECT ISOLATION on failure
# ---------------------------------------------------------------------------


def test_per_project_isolation_on_failure(source_url, target):
    report = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(BETA, GAMMA)))
    by_slug = {p.slug: p for p in report.projects}

    assert by_slug[BETA].status == "migrated"  # commits despite a WARN (dangling cred)
    assert by_slug[GAMMA].status == "failed"  # invalid bug.severity → its tree rolls back
    assert report.overall == "partial"
    assert report.exit_code == 1

    assert _project_row(target.engine, BETA) is not None
    assert _project_row(target.engine, GAMMA) is None  # rolled back, not partially written
    # Whole-tree rollback: only beta's single version survives (gamma's did NOT leak).
    assert _scalar(target.engine, "SELECT COUNT(*) FROM versions") == 1


# ---------------------------------------------------------------------------
# 12. MISSING USER — fail-closed, one case per referencing column, target untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", list(ALL_USER_IDS.keys()))
def test_missing_user_fails_closed(source_url, target, column):
    missing_uid = ALL_USER_IDS[column]
    with target.engine.begin() as c:
        c.execute(text("DELETE FROM users WHERE id=:i"), {"i": missing_uid})

    before = _scalar(target.engine, "SELECT COUNT(*) FROM projects")
    with pytest.raises(MigrationPreflightError) as exc:
        # alpha references ALL five columns, so omitting any one trips the pre-flight.
        run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    assert str(missing_uid) in exc.value.missing_user_ids

    after = _scalar(target.engine, "SELECT COUNT(*) FROM projects")
    assert before == after == 0  # fail-closed BEFORE any write — target untouched


# ---------------------------------------------------------------------------
# 14. CREDENTIAL POINTER — present file OK vs missing file WARN (non-blocking)
# ---------------------------------------------------------------------------


def test_credential_pointer_present_is_ok(source_url, target):
    report = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(ALPHA,)))
    res = report.projects[0]
    assert res.status == "migrated"
    dangling = [f for f in res.findings if f["code"] == "dangling_credential_pointer"]
    assert dangling == []  # alpha's credential file exists → no WARN, content never read


def test_credential_pointer_missing_warns_but_migrates(source_url, target):
    report = run_migration(_cfg(source_url, target, dry_run=False, only_slugs=(BETA,)))
    res = report.projects[0]
    assert res.status == "migrated"  # WARN is non-critical — migration still succeeds
    dangling = [f for f in res.findings if f["code"] == "dangling_credential_pointer"]
    assert len(dangling) == 1
    assert res.counts["credentials"] == 1  # registry pointer copied even though the file is absent
