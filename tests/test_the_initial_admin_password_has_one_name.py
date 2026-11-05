"""DEV-300 — the password an app founds its first administrator with is the „Inicializačné heslo administrátora“.

03.11.2026 the customer's form called it „Tajný kľúč (pre zákazníka)“, the refused deploy „prihlasovacie heslo
administrátora … heslo zákazníka“ — two names for one field, neither the one the people use. Director 04.11.2026: „Už
desiatky rokov používame tento pojem, každý z nás vie čo je to, iné pojmy by bolo treba vysvetľovať. Ani ja na začiatku
som nevedel čo je to tajný kľúč.“ The name stands once in the backend and once in the frontend, and they are the same;
no text for a person in the cockpit says „tajný kľúč“ or „začiatočné heslo“.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import env_contract

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_NAME = re.compile(r'export const ADMIN_PASSWORD_LABEL = "([^"]+)"')
OLD_NAMES = re.compile(r"tajn\w* kľúč|začiatočn\w* hesl", re.IGNORECASE)


def _texts() -> list[Path]:
    frontend = [
        p for p in (ROOT / "frontend" / "src").rglob("*") if p.suffix in (".ts", ".tsx") and "__tests__" not in p.parts
    ]
    backend = [p for p in (ROOT / "backend").rglob("*.py") if "tests" not in p.parts]
    return frontend + backend


def test_the_backend_and_the_frontend_call_it_the_same():
    found = FRONTEND_NAME.search((ROOT / "frontend" / "src" / "types" / "customer.ts").read_text(encoding="utf-8"))

    assert env_contract.ADMIN_PASSWORD_LABEL == "Inicializačné heslo administrátora"
    assert found and found.group(1) == env_contract.ADMIN_PASSWORD_LABEL


def test_no_text_of_the_cockpit_uses_an_old_name():
    stale = [
        f"{path.relative_to(ROOT)}: {match.group(0)}"
        for path in _texts()
        for match in OLD_NAMES.finditer(path.read_text(encoding="utf-8"))
    ]

    assert stale == []


def test_the_rule_sees_the_old_names():
    for old in ("Tajný kľúč (pre zákazníka)", "zmeň tajný kľúč", "tajného kľúča", "Začiatočné heslo správcu"):
        assert OLD_NAMES.search(old), old


async def test_a_deploy_without_it_names_the_field_and_where_to_set_it(db_session, monkeypatch, tmp_path):
    import pytest

    from backend.services import claude_agent
    from backend.services import deploy as deploy_service
    from tests.test_deploy_service import _FakeRunner, _make_customer, _make_project, _make_user, _make_version

    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    (tmp_path / project.slug).mkdir()
    (tmp_path / project.slug / ".env.example").write_text("ADMIN_INITIAL_PASSWORD=\n", encoding="utf-8")
    runner = _FakeRunner()

    with pytest.raises(ValueError) as refused:
        await deploy_service.deploy(
            db_session, customer.id, version_number="0.1.0", environment="uat", actor_id=user.id, deploy_runner=runner
        )

    assert str(refused.value) == (
        "Nasadenie sa nedá spustiť: táto aplikácia potrebuje inicializačné heslo administrátora, ale zákazník ho nemá "
        "nastavené. Nastav ho v Zákazníci → Upraviť zákazníka → „Inicializačné heslo administrátora“ a skús to znova."
    )
    assert runner.calls == []
