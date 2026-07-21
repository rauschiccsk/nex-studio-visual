"""Vizuál→build binding (v4.0.23): the build faithfully reproduces the approved Vizuál.

When a Version's Vizuál is approved, its FE commit is recorded (``vizual_approved_sha``) as
the binding contract. The Auditor's Verifikácia brief then carries a fidelity check (diff the
delivered FE against that commit — a redesigned/gutted approved screen = FAIL), and the AI
Agent + Auditor charters carry the matching standing rules ("preber, neprerábaj" / verify).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATES = _REPO_ROOT / "templates"


def _make_version(db, *, vizual_sha=None) -> Version:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"cc_{s}", email=f"cc_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"P{s}",
        slug=f"p-{s}",
        type="standard",
        auth_mode="password",
        description="binding test",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(
        project_id=project.id,
        version_number="0.1.0",
        status="active",
        vizual_approved_sha=vizual_sha,
    )
    db.add(version)
    db.flush()
    return version


def test_verifikacia_directive_checks_vizual_fidelity_when_approved(db_session) -> None:
    v = _make_version(db_session, vizual_sha="abc123def456")
    d = orchestrator._verifikacia_directive(db_session, v.id)
    assert "VERNOSŤ VIZUÁLU" in d
    assert "abc123def456" in d  # the approved-Vizuál commit is threaded for the git diff
    assert "git diff" in d


def test_verifikacia_directive_omits_fidelity_when_no_vizual(db_session) -> None:
    v = _make_version(db_session, vizual_sha=None)
    d = orchestrator._verifikacia_directive(db_session, v.id)
    # No approved Vizuál (e.g. a flow that never entered the phase) → no fidelity check.
    assert "VERNOSŤ VIZUÁLU" not in d


def test_charters_carry_vizual_preserve_and_verify_rules() -> None:
    agent = (_TEMPLATES / "ai-agent-charter.md").read_text(encoding="utf-8")
    auditor = (_TEMPLATES / "auditor-charter.md").read_text(encoding="utf-8")
    # AI Agent: preserve the approved Vizuál FE during Programovanie.
    assert "PREBERÁŠ, NEPRERÁBAŠ" in agent
    # Auditor: verify the delivered FE matches the approved Vizuál.
    assert "Vernosť schválenému Vizuálu" in auditor
