"""DEV-164 — the customer form reads every kind of setting an app may mark, a Web project's included.

22.10.2026 Career Asistent 1.1.0, a Web project, could not be deployed to UAT: a required setting was missing, and the
form to enter it showed no settings at all. ``GET /projects/career-asistent/customer-settings`` answered 500 — the
administration's address (``public_url_admin``, DEV-164) was read from ``.env.example`` but missing from the answer's
schema — and the screen took the failure for „the app asks nothing“. Director 22.10.2026: „Súhlasím“.
"""

from __future__ import annotations

import typing
import uuid

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.customer import CustomerSettingRead
from backend.services import claude_agent, env_contract

#: Career Asistent 1.1.0's markers (22.10.2026), values left out.
CAREER_ENV_EXAMPLE = """\
# @public-url
APP_PUBLIC_URL=
# @public-url-admin
ADMIN_PUBLIC_URL=
# @customer required: „Prihlasovacie meno prvého správcu (odporúčané admin)“
BOOTSTRAP_ADMIN_USERNAME=
# @customer-secret required: „Inicializačné heslo prvého správcu“
BOOTSTRAP_ADMIN_PASSWORD=
"""


def test_the_answer_knows_every_kind_the_contract_reads():
    """The kinds come from the reader of the markers — a kind added there and not here is a 500 for the form."""
    answered = set(typing.get_args(CustomerSettingRead.model_fields["kind"].annotation))

    assert answered == set(env_contract._KINDS.values())


def test_a_web_projects_settings_reach_the_customer_form(client, db_session, monkeypatch, tmp_path):
    owner = User(
        username=f"web_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@test.local", password_hash="x", role="ri"
    )
    db_session.add(owner)
    db_session.flush()
    project = Project(
        name="Career Asistent",
        slug=f"career-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="a Web project",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    (tmp_path / project.slug).mkdir()
    (tmp_path / project.slug / ".env.example").write_text(CAREER_ENV_EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)

    answer = client.get(f"/api/v1/projects/{project.slug}/customer-settings")

    assert answer.status_code == 200, answer.text
    assert [(s["key"], s["kind"], s["required"]) for s in answer.json()["settings"]] == [
        ("APP_PUBLIC_URL", "public_url", False),
        ("ADMIN_PUBLIC_URL", "public_url_admin", False),
        ("BOOTSTRAP_ADMIN_USERNAME", "customer", True),
        ("BOOTSTRAP_ADMIN_PASSWORD", "customer_secret", True),
    ]
    assert answer.json()["settings"][2]["label"] == "Prihlasovacie meno prvého správcu (odporúčané admin)"
