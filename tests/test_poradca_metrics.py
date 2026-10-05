"""Náklady: Poradca má vlastný riadok, zvlášť od fáz stavby (ICCINT-167, návrh §3 „Náklady a limit")."""

from __future__ import annotations

import uuid

from backend.db.models.foundation import User
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import metrics


def _setup(db):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e.com", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    v1 = Version(project_id=project.id, version_number="1.0.0")
    v2 = Version(project_id=project.id, version_number="1.1.0")
    db.add_all([v1, v2])
    db.flush()
    conv = PoradcaConversation(
        project_id=project.id, version_id=v2.id, author_id=user.id, title="t", claude_session_id=uuid.uuid4()
    )
    db.add(conv)
    db.flush()

    def answer(version, inp, out):
        db.add(
            PoradcaMessage(
                conversation_id=conv.id,
                version_id=version.id if version else None,
                author="poradca",
                content="a",
                status="done",
                usage={"input_tokens": inp, "output_tokens": out, "model": "claude-opus-test"},
                duration_seconds=10.0,
            )
        )

    answer(v1, 100, 10)  # otázka padla, kým rozhovor bol pri 1.0.0
    answer(v2, 200, 20)
    answer(None, 400, 40)  # celý projekt
    db.add(PoradcaMessage(conversation_id=conv.id, author="human", content="q", status="done"))
    db.flush()
    return project, v1, v2


def test_poradca_row_per_scope_and_never_a_human_figure(db_session):
    project, v1, v2 = _setup(db_session)
    result = metrics.compute_project_metrics(db_session, project)

    cumulative = [r for r in result.rows if r.kind == "poradca"]
    assert len(cumulative) == 1
    row = cumulative[0]
    assert row.key == "poradca" and row.turns == 3
    assert (row.input_tokens, row.output_tokens) == (700, 70)
    assert row.human_minutes is None and row.human_cost is None
    # Do nameraného súčtu patrí; ručne zadané nie je.
    assert result.totals.input_tokens_measured == 700 and result.totals.input_tokens_external == 0

    by_version = {v.version_number: v for v in result.by_version}
    v1_rows = [r for r in by_version["1.0.0"].rows if r.kind == "poradca"]
    v2_rows = [r for r in by_version["1.1.0"].rows if r.kind == "poradca"]
    # Verzia sčíta len otázky, ktoré na ňu naozaj padli — nie podľa súčasnej voľby rozhovoru.
    assert v1_rows[0].input_tokens == 100
    assert v2_rows[0].input_tokens == 200


def test_poradca_row_sits_before_system_after_external(db_session):
    project, _v1, _v2 = _setup(db_session)
    kinds = [r.kind for r in metrics.compute_project_metrics(db_session, project).rows]
    assert kinds.index("poradca") == len([k for k in kinds if k in ("phase", "external")])
