"""Unit tests for the migration pre-flight guards (SEPARATE assertions).

Covers, per the STEP 8 design:
  (a) source==target refusal (guard 1)          — pure URL check
  (b) prod-target refusal unless override (g. 2) — pure URL check, SEPARATE from (a)
  (c) FULL referenced-user existence (guard 3)   — collection over all five
      referencing columns + fail-closed abort, one case each for
      projects.created_by / projects.owner_id / bugs.created_by /
      deploy_events.actor_id / project_members.user_id
  (d) report serialization carries NO secret material.

The genuine two-DB "target untouched" fail-closed is additionally proven end to
end by tests/integration/test_migration_e2e.py (test #12); here the per-column
abort is isolated by seeding a valid v2 tree (SAVEPOINT) and driving the
existence result directly, since the v2 schema's FKs forbid a dangling in-DB user
reference.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.project_member import ProjectMember
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic  # noqa: F401 — ensures model registration
from backend.services.migration import runner

# ---------------------------------------------------------------------------
# (a) source != target
# ---------------------------------------------------------------------------


def test_assert_source_target_distinct_raises_on_same_db():
    with pytest.raises(RuntimeError):
        runner.assert_source_target_distinct(
            "postgresql+pg8000://u:p@h:9178/nexstudio",
            "postgresql+pg8000://u:p@h:9198/nexstudio",  # same DB NAME
        )


def test_assert_source_target_distinct_passes_on_distinct_db():
    runner.assert_source_target_distinct(
        "postgresql+pg8000://u:p@h:9178/nexstudio_v1",
        "postgresql+pg8000://u:p@h:9198/nexstudio_v2",
    )


# ---------------------------------------------------------------------------
# (b) target != cockpit PROD name — SEPARATE guard
# ---------------------------------------------------------------------------


def test_assert_target_not_prod_raises_when_target_is_prod_name(monkeypatch):
    # settings.database_url DB name is the cockpit PROD name.
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+pg8000://u:p@h/nexstudio")
    with pytest.raises(runner.MigrationGuardError):
        runner.assert_target_not_prod("postgresql+pg8000://u:p@h/nexstudio", allow_prod_target=False)


def test_assert_target_not_prod_allows_with_override(monkeypatch):
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+pg8000://u:p@h/nexstudio")
    # Explicit operator override.
    runner.assert_target_not_prod("postgresql+pg8000://u:p@h/nexstudio", allow_prod_target=True)


def test_assert_target_not_prod_passes_for_test_db(monkeypatch):
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+pg8000://u:p@h/nexstudio")
    # nexstudio_test != nexstudio → passes without the override.
    runner.assert_target_not_prod("postgresql+pg8000://u:p@h/nexstudio_test", allow_prod_target=False)


# ---------------------------------------------------------------------------
# (c) FULL referenced-user existence
# ---------------------------------------------------------------------------


def _seed_tree_with_five_roles(db_session):
    """Seed a valid v2 tree whose referenced users cover all five columns.

    Returns (project_id, role_ids) where role_ids maps a label to the seeded
    user id for each referencing column.
    """

    def _user(label: str) -> User:
        u = User(
            username=f"{label}_{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            role="ri",
        )
        db_session.add(u)
        db_session.flush()
        return u

    creator = _user("creator")
    owner = _user("owner")
    bug_creator = _user("bug")
    actor = _user("actor")
    member = _user("member")

    project = Project(
        name=f"Proj {uuid.uuid4().hex[:6]}",
        slug=f"proj-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="guard test",
        created_by=creator.id,
        owner_id=owner.id,
    )
    db_session.add(project)
    db_session.flush()

    from backend.db.models.bugs import Bug
    from backend.db.models.versions import Version

    version = Version(project_id=project.id, version_number="v1.0.0", status="active")
    db_session.add(version)
    db_session.flush()

    bug = Bug(
        project_id=project.id,
        version_id=version.id,
        bug_number=1,
        title="b",
        description="d",
        severity="major",
        created_by=bug_creator.id,
    )
    db_session.add(bug)

    customer = Customer(project_id=project.id, name="Cust", slug="cust")
    db_session.add(customer)
    db_session.flush()

    deploy = DeployEvent(
        customer_id=customer.id,
        project_id=project.id,
        version_number="v1.0.0",
        environment="uat",
        event_type="deploy",
        status="ok",
        actor_id=actor.id,
    )
    db_session.add(deploy)

    membership = ProjectMember(project_id=project.id, user_id=member.id, role="member")
    db_session.add(membership)
    db_session.flush()

    return project.id, {
        "created_by": creator.id,
        "owner_id": owner.id,
        "bugs.created_by": bug_creator.id,
        "deploy_events.actor_id": actor.id,
        "project_members.user_id": member.id,
    }


def test_collect_referenced_user_ids_gathers_all_five_columns(db_session):
    project_id, role_ids = _seed_tree_with_five_roles(db_session)
    conn = db_session.connection()
    tables = runner.reflect_source_tables(conn)
    refs = runner.collect_referenced_user_ids(conn, tables, [project_id])
    assert refs == set(role_ids.values())


@pytest.mark.parametrize(
    "column",
    ["created_by", "owner_id", "bugs.created_by", "deploy_events.actor_id", "project_members.user_id"],
)
def test_find_missing_users_flags_each_referencing_column(db_session, column):
    _project_id, role_ids = _seed_tree_with_five_roles(db_session)
    referenced = set(role_ids.values())
    missing_uid = role_ids[column]
    existing = referenced - {missing_uid}
    assert runner.find_missing_users(referenced, existing) == {missing_uid}


def test_assert_referenced_users_exist_passes_when_all_present(db_session):
    project_id, _ = _seed_tree_with_five_roles(db_session)
    conn = db_session.connection()
    tables = runner.reflect_source_tables(conn)
    # No raise — all five users exist in the (same) target.
    runner.assert_referenced_users_exist(conn, tables, conn, [project_id])


@pytest.mark.parametrize(
    "column",
    ["created_by", "owner_id", "bugs.created_by", "deploy_events.actor_id", "project_members.user_id"],
)
def test_assert_referenced_users_exist_fails_closed_per_column(db_session, monkeypatch, column):
    project_id, role_ids = _seed_tree_with_five_roles(db_session)
    conn = db_session.connection()
    tables = runner.reflect_source_tables(conn)
    missing_uid = role_ids[column]

    # Drive the target-existence result to omit exactly this column's user, isolating
    # the fail-closed abort (the v2 schema's FKs forbid an actual dangling in-DB ref).
    real_existing = runner.existing_user_ids

    def _existing_minus_one(target_conn, candidate_ids):
        return real_existing(target_conn, candidate_ids) - {missing_uid}

    monkeypatch.setattr(runner, "existing_user_ids", _existing_minus_one)

    with pytest.raises(runner.MigrationPreflightError) as exc:
        runner.assert_referenced_users_exist(conn, tables, conn, [project_id])
    assert str(missing_uid) in exc.value.missing_user_ids


# ---------------------------------------------------------------------------
# (d) report serialization contains NO secret material
# ---------------------------------------------------------------------------


def test_report_serialization_has_no_secret_material():
    result = runner.ProjectResult(
        slug="alpha",
        status="dry_run",
        reason="rolled back (dry-run)",
        counts={"versions": 2, "epics": 3, "credentials": 1},
        findings=[
            {
                "table": "credentials",
                "severity": "WARN",
                "code": "dangling_credential_pointer",
                "detail": "file missing on disk",
                "file_path": "/opt/data/nex-studio/credentials/alpha.md",
            }
        ],
    )
    report = runner.MigrationReport(dry_run=True, source_db="src", target_db="tgt", projects=[result])
    serialized = json.dumps(report.to_json())

    # Only path/metadata may appear — never file CONTENT. The finding schema is
    # closed to a known key set; a 'content' key would mean a leak.
    for finding in report.projects[0].findings:
        assert set(finding.keys()) <= {"table", "severity", "code", "detail", "file_path", "id"}
        assert "content" not in finding
    # The registry only ever carries the pointer path — assert no accidental secret token.
    assert "hunter2" not in serialized
    assert "password_hash" not in serialized
