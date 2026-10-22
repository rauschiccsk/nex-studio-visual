"""DEV-181 — Poradca reads every screen of the project the Manažér reads.

22.10.2026 Career Asistent 1.1.0: Poradca could not name the Auditor's second note (it lives in the verdict's data, the
message only counts the notes), saw 200 characters of a requirement, and saw neither the refused UAT deploy nor the
one that came out „neoverené“ — twice it asked the Director to copy the message for it. Director 22.10.2026: „Poradca
v projekte musí vidieť všetko.“

The guard reads the project's screens from the cockpit's menu (an item that falls back to the projects list without a
project) and asks that each has its Poradca tools in ``tools.SCREENS`` or a written reason in ``tools.NOT_READ``.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.db.models.backlog import BacklogItem
from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent, DeployRefusal
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.services import customer as customer_service
from backend.services.poradca import tools
from backend.services.poradca.mcp_server import ToolError
from tests.test_poradca_tools_db import world  # noqa: F401 — the fixture is used by name

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx"
CHARTER = ROOT / "templates" / "poradca-charter.md"


def _project_screens() -> set[str]:
    """Every menu item that needs a project — without one it falls back to the projects list."""
    text = SIDEBAR.read_text(encoding="utf-8")
    screens = set()
    # One item runs from its <NavItem to the next one — its icon is JSX too, so „/>“ does not end it.
    for item in re.split(r"(?=<NavItem\b)", text)[1:]:
        label = re.search(r'label="([^"]+)"', item)
        if label and "projectsFallback" in item:
            screens.add(label.group(1))
    return screens


def _tool_names() -> set[str]:
    return {t.name for t in tools.build_tools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())}


def test_every_screen_of_a_project_has_its_poradca_tools():
    screens = _project_screens()

    assert {"Zákazníci", "UAT", "PROD", "Zásobník"} <= screens, (
        "the menu was read wrong — the guard would guard nothing"
    )
    assert screens == set(tools.SCREENS) | set(tools.NOT_READ)


def test_each_tool_a_screen_names_is_one_poradca_has():
    named = {name for names in tools.SCREENS.values() for name in names} - {tools.FILES}

    assert named - _tool_names() == set()
    assert all(tools.SCREENS.values()), "a screen with no tool is a screen Poradca does not read"


def test_the_charter_names_every_tool_in_its_table_of_tools():
    """In the table's first column — a name the charter mentions elsewhere (a hint under the table) is not a row."""
    rows = [line for line in CHARTER.read_text(encoding="utf-8").splitlines() if line.startswith("| `")]
    in_table = {name for row in rows for name in re.findall(r"`([a-z_]+)`", row.split("|")[1])}

    assert sorted(_tool_names() - in_table) == []


# ── what the tools return ────────────────────────────────────────────────────────────────────────────────────────


async def test_stavba_names_every_note_of_the_auditors_verdict(world):  # noqa: F811
    db, version = world["db"], world["version"]
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="verifikacia",
            current_actor="auditor",
            status="awaiting_manazer",
            next_action="Verifikácia PASS — schváľ na Hotovo.",
        )
    )
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content="Verzia je overená. Ostávajú dve neblokujúce poznámky na otvrdenie pred verejným spustením.",
            payload={
                "verdict": "PASS",
                "findings": [
                    {"text": "Poistku proti registráciám sa dá obísť.", "blocking": False, "escapes_tests": True},
                    {"text": "Spojenie so zbernou schránkou neoveruje server.", "blocking": False},
                ],
            },
        )
    )
    db.flush()

    out = await world["tools"].stavba({})

    assert "  poznámka Audítora (neblokuje, skúšky ju nechytia): Poistku proti registráciám sa dá obísť." in out
    assert "  poznámka Audítora (neblokuje): Spojenie so zbernou schránkou neoveruje server." in out


async def test_poziadavka_gives_the_whole_requirement(world):  # noqa: F811
    db, project = world["db"], world["project"]
    long_text = "Pred verejným spustením treba limit podľa skutočnej IP. " * 20
    db.add(BacklogItem(project_id=project.id, number=2, title="Limit registrácií", description=long_text))
    db.flush()

    out = await world["tools"].poziadavka({"cislo": 2})

    assert out.splitlines()[0] == "REQ-2 [Otvorené, priorita stredná] Limit registrácií"
    assert long_text.strip() in out
    with pytest.raises(ToolError, match="REQ-9"):
        await world["tools"].poziadavka({"cislo": 9})


async def test_verzie_lists_the_versions_with_their_builds(world):  # noqa: F811
    db, version = world["db"], world["version"]
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="blocked",
            block_reason="decision_needed",
            next_action="x",
        )
    )
    db.flush()

    out = await world["tools"].verzie({})

    assert "1.2.0 — " in out and "stavba: fáza programovanie, stav blocked, dôvod zastavenia decision_needed" in out


ENV_EXAMPLE = """\
# @public-url
APP_PUBLIC_URL=
# @customer required: „Prihlasovacie meno prvého správcu (odporúčané admin)“
BOOTSTRAP_ADMIN_USERNAME=
# @customer-secret required: „Inicializačné heslo prvého správcu“
BOOTSTRAP_ADMIN_PASSWORD=
# @customer: „Server odchádzajúcej pošty“
SMTP_HOST=
"""


@pytest.fixture
def icc(world, monkeypatch):  # noqa: F811
    db, project = world["db"], world["project"]
    (world["dir"] / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    customer = Customer(
        project_id=project.id, name="ICC s.r.o.", slug="icc", subdomain="icc", settings={"SMTP_HOST": "mail.icc.sk"}
    )
    db.add(customer)
    db.flush()
    secrets = {"BOOTSTRAP_ADMIN_PASSWORD": "TAJNE-HESLO-ICC"}
    monkeypatch.setattr(customer_service, "read_secret_settings", lambda db, c: dict(secrets))
    monkeypatch.setattr(customer_service, "secret_setting_keys", lambda db, c: sorted(secrets))
    return customer


async def test_zakaznici_says_what_each_customer_has_and_lacks_never_a_secret(world, icc):  # noqa: F811
    out = await world["tools"].zakaznici({})

    assert "ICC s.r.o. (skratka icc, subdoména icc)" in out
    assert "  Server odchádzajúcej pošty (SMTP_HOST): mail.icc.sk" in out
    assert "  Inicializačné heslo prvého správcu (BOOTSTRAP_ADMIN_PASSWORD, povinné): uložené" in out
    assert "  Prihlasovacie meno prvého správcu (odporúčané admin) (BOOTSTRAP_ADMIN_USERNAME, povinné): chýba" in out
    assert "  Nasadenie sa nedá spustiť: Zákazníkovi chýbajú povinné nastavenia: Prihlasovacie meno" in out
    assert "TAJNE-HESLO-ICC" not in out


async def test_nasadenia_names_the_deploys_their_warnings_and_the_refused_ones(world, icc):  # noqa: F811
    db, project = world["db"], world["project"]
    db.add(
        DeployEvent(
            customer_id=icc.id,
            project_id=project.id,
            version_number="1.1.0",
            environment="uat",
            event_type="deploy",
            status="unverified",
            detail="frontend 'web' not serving within 120s",
            warnings=["Projekt má služby, ktoré inštalácia nemá: web."],
        )
    )
    db.add(
        DeployRefusal(
            customer_id=icc.id,
            project_id=project.id,
            version_number="1.1.0",
            environment="uat",
            reason="Nasadenie sa nedá spustiť: Zákazníkovi chýbajú povinné nastavenia: Prihlasovacie meno.",
        )
    )
    db.flush()

    out = await world["tools"].nasadenia({"prostredie": "uat"})

    assert "ICC s.r.o. (icc):" in out
    assert "UAT nasadenie 1.1.0: unverified — frontend 'web' not serving within 120s" in out
    assert "    upozornenie: Projekt má služby, ktoré inštalácia nemá: web." in out
    assert "UAT nasadenie 1.1.0 ODMIETNUTÉ — Nasadenie sa nedá spustiť: Zákazníkovi chýbajú" in out
    assert "nič" in await world["tools"].nasadenia({"prostredie": "prod"})
    with pytest.raises(ToolError):
        await world["tools"].nasadenia({"prostredie": "staging"})


async def test_naklady_fakturacia_and_plane_tikety_read_the_screens_services(world, monkeypatch):  # noqa: F811
    from backend.services import delivery_statement, metrics, plane_tickets

    totals = SimpleNamespace(agent_cost_total=42, agent_cost_complete=True)
    monkeypatch.setattr(
        metrics,
        "compute_project_metrics",
        lambda db, project: SimpleNamespace(
            by_version=[SimpleNamespace(version_number="1.2.0", status="active", totals=totals)], totals=totals
        ),
    )
    row = SimpleNamespace(
        version=SimpleNamespace(version_number="1.2.0"),
        state="ready",
        reason=None,
        valid=None,
        preview=SimpleNamespace(amounts=SimpleNamespace(total="1250.00")),
    )
    monkeypatch.setattr(delivery_statement, "billing_rows", lambda db, project, root: [row])
    ticket = SimpleNamespace(
        identifier="DEV-9", state="Todo", title="Limit podľa IP", requirement="REQ-2", warning=None
    )
    monkeypatch.setattr(plane_tickets, "gateway", lambda: None)
    monkeypatch.setattr(
        plane_tickets,
        "project_tickets",
        lambda db, gw, project, closed: plane_tickets.ProjectTickets(module="Career Asistent", tickets=[ticket]),
    )

    assert "1.2.0 (active): 42 €" in await world["tools"].naklady({})
    assert "1.2.0: dá sa vystaviť, na vystavenie 1250.00 €" in await world["tools"].fakturacia({})
    assert "DEV-9 [Todo] Limit podľa IP — REQ-2" in await world["tools"].plane_tikety({})
