"""Požiadavka → koncept novej verzie (``change_request.mint_from_request``, ICCINT-167).

Prenesené zo skúšok odstránenej Konzultácie: spoločnú cestu dnes používa Poradcovo tlačidlo „Založiť novú
verziu z tejto požiadavky" a správanie má ostať rovnaké — koncept bez stavby, požiadavka v zásobníku
priradená k verzii, zadanie z jej textu, číslo verzie o jedno vyššie.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import change_request


def _project(db, first_version: str) -> tuple[User, Project]:
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
    db.add(Version(project_id=project.id, version_number=first_version, status="released"))
    db.flush()
    return user, project


def test_mint_creates_a_draft_version_with_the_request_linked_and_no_build(db_session):
    user, project = _project(db_session, "1.0.0")
    item, version = change_request.mint_from_request(
        db_session, project_id=project.id, summary="Pridať export faktúr do XLSX.", title="XLSX export", user_id=user.id
    )
    assert version.version_number == "1.1.0"
    assert version.status == "planned" and version.project_id == project.id
    assert version.description == "Pridať export faktúr do XLSX."
    assert (
        db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one_or_none()
        is None
    )
    req = db_session.get(BacklogItem, item.id)
    assert (req.title, req.description) == ("XLSX export", "Pridať export faktúr do XLSX.")
    assert req.version_id == version.id and req.status == "included"


def test_mint_title_falls_back_to_the_summary_and_the_major_rolls(db_session):
    user, project = _project(db_session, "1.9.0")
    item, version = change_request.mint_from_request(
        db_session,
        project_id=project.id,
        summary="Zmena správania pri duplicitných faktúrach.",
        title="",
        user_id=user.id,
    )
    assert version.version_number == "2.0.0"
    assert db_session.get(BacklogItem, item.id).title == "Zmena správania pri duplicitných faktúrach."
