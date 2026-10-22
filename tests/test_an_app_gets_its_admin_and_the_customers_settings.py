"""DEV-72 — a deployed app gets the admin login and the settings the manager entered for the customer.

12.10.2026 the manager registered ICC s.r.o. as a customer of career-asistent. The cockpit hands an app the customer's
secret only as ``ADMIN_INITIAL_PASSWORD``; career-asistent creates its first admin from ``BOOTSTRAP_ADMIN_EMAIL`` and
``BOOTSTRAP_ADMIN_PASSWORD``. The deploy would have left the e-mail unset and the password random — no admin, nobody
could sign in — and nothing stopped it. The app now marks in its ``.env.example`` what the customer provides
(``@customer``, ``@customer-secret``, ``required``) and where its public address goes (``@public-url``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.schemas.customer import CustomerCreate, CustomerUpdate
from backend.services import claude_agent, env_contract, uat_provisioner
from backend.services import customer as customer_service
from backend.services import deploy as deploy_service
from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer, _make_project, _make_user, _make_version

#: career-asistent's ``.env.example`` as it was on 12.10.2026 (the admin part), without markers.
CAREER_1210 = """\
APP_PUBLIC_URL=https://career.example.com
SESSION_SECRET=
# First admin, created at start only when no admin exists (design.md §4).
BOOTSTRAP_ADMIN_EMAIL=
BOOTSTRAP_ADMIN_PASSWORD=
SMTP_HOST=
SMTP_PASSWORD=
"""

#: The same file with the markers the fast fix adds.
CAREER_MARKED = """\
# @public-url
APP_PUBLIC_URL=https://career.example.com
SESSION_SECRET=
# First admin, created at start only when no admin exists (design.md §4).
# @customer required: „E-mail prvého správcu — ním sa prvý raz prihlásiš"
BOOTSTRAP_ADMIN_EMAIL=
# @customer-secret required: „Heslo prvého správcu"
BOOTSTRAP_ADMIN_PASSWORD=
# @customer: "Server odchádzajúcej pošty"
SMTP_HOST=
# @customer-secret: „Heslo k odchádzajúcej pošte“
SMTP_PASSWORD=
"""

#: NEX Manager seeds its own first admin (a documented default password, changed at first login).
MANAGER_1210 = (
    "BCRYPT_ROUNDS=12\n# Seed admin email (attribute, not login) — change after first login\nSEED_ADMIN_EMAIL=admin@x\n"
)


# --- the contract ----------------------------------------------------------------------------------------------------


def test_the_markers_say_who_provides_what():
    contract = env_contract.parse(CAREER_MARKED)

    assert [(s.key, s.kind, s.required, s.label) for s in contract.settings] == [
        ("APP_PUBLIC_URL", "public_url", False, "APP_PUBLIC_URL"),
        ("BOOTSTRAP_ADMIN_EMAIL", "customer", True, "E-mail prvého správcu — ním sa prvý raz prihlásiš"),
        ("BOOTSTRAP_ADMIN_PASSWORD", "customer_secret", True, "Heslo prvého správcu"),
        ("SMTP_HOST", "customer", False, "Server odchádzajúcej pošty"),
        ("SMTP_PASSWORD", "customer_secret", False, "Heslo k odchádzajúcej pošte"),
    ]
    assert contract.problems == ()


def test_a_marker_that_means_nothing_is_reported_not_ignored():
    contract = env_contract.parse("# @custommer: „x“\nA=\n\n# @customer: „y“\n\nB=\n# @customer\n# @public-url\nC=\n")

    assert contract.problems == (
        "neznáma značka „@custommer“ na riadku 1",
        "značka na riadku 4 nestojí nad žiadnou premennou",
        "premenná C má viac značiek naraz",
    )
    assert env_contract.deploy_blockers(contract, template_keys=["A", "B", "C"], values={})[0].startswith(
        "Nastavenia aplikácie (.env.example) sa nedajú prečítať: neznáma značka"
    )


def test_the_admin_password_of_12_10_would_have_been_random_and_stops_the_deploy():
    blockers = env_contract.deploy_blockers(
        env_contract.parse(CAREER_1210), template_keys=list(_keys(CAREER_1210)), values={}
    )

    assert len(blockers) == 1
    assert blockers[0].startswith("Heslo správcu aplikácie by dostalo náhodnú hodnotu, ktorú nikto nepozná (")
    assert "(BOOTSTRAP_ADMIN_PASSWORD)" in blockers[0]


def test_an_app_that_seeds_its_own_admin_deploys_as_before():
    """NEX Manager: no admin secret to be randomised — nothing to stop."""
    assert (
        env_contract.deploy_blockers(
            env_contract.parse(MANAGER_1210), template_keys=list(_keys(MANAGER_1210)), values={}
        )
        == []
    )
    legacy = "ADMIN_INITIAL_PASSWORD=x\n"
    assert (
        env_contract.deploy_blockers(env_contract.parse(legacy), template_keys=["ADMIN_INITIAL_PASSWORD"], values={})
        == []
    )


def test_a_required_setting_without_a_value_is_named_by_its_text():
    contract = env_contract.parse(CAREER_MARKED)
    keys = list(_keys(CAREER_MARKED))

    blockers = env_contract.deploy_blockers(contract, template_keys=keys, values={"BOOTSTRAP_ADMIN_EMAIL": "a@icc.sk"})
    assert blockers == ["Zákazníkovi chýbajú povinné nastavenia: Heslo prvého správcu. Doplň ich v sekcii Zákazníci."]
    full = {"BOOTSTRAP_ADMIN_EMAIL": "a@icc.sk", "BOOTSTRAP_ADMIN_PASSWORD": "pw"}
    assert env_contract.deploy_blockers(contract, template_keys=keys, values=full) == []


def _keys(text: str) -> dict[str, str]:
    return {line.split("=", 1)[0]: "" for line in text.splitlines() if line and not line.startswith("#")}


# --- the render ------------------------------------------------------------------------------------------------------


def _render(contract_text: str, values=None, existing=None, preserved=None) -> dict[str, str]:
    contract = env_contract.parse(contract_text)
    out = uat_provisioner.generate_uat_env(
        slug="uat-icc-career-asistent",
        project="career-asistent",
        version="0.1.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example=_example_values(contract_text),
        db_user="u",
        db_name="d",
        shared_db_password="p",
        existing_env=existing,
        preserved_secrets=preserved,
        customer_keys=frozenset(contract.customer_keys),
        customer_values=values,
        public_url_keys=frozenset(contract.public_url_keys),
        public_url="https://uat-icc-career-asistent.isnex.eu",
    )
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line and not line.startswith("#"))


def _example_values(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))


def test_the_customers_values_and_the_public_address_reach_the_installation():
    env = _render(
        CAREER_MARKED, values={"BOOTSTRAP_ADMIN_EMAIL": "spravca@icc.sk", "BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1"}
    )

    assert env["BOOTSTRAP_ADMIN_EMAIL"] == "spravca@icc.sk"
    assert env["BOOTSTRAP_ADMIN_PASSWORD"] == "Heslo-1"
    assert env["APP_PUBLIC_URL"] == "https://uat-icc-career-asistent.isnex.eu"
    assert len(env["SESSION_SECRET"]) >= 32, "a machine secret nobody enters stays random"


def test_a_customer_setting_nobody_entered_is_never_random():
    """SMTP_PASSWORD ends in _PASSWORD: before DEV-72 it got a random value — mail that looks configured and fails."""
    env = _render(CAREER_MARKED, values={})
    assert "SMTP_PASSWORD" not in env and "BOOTSTRAP_ADMIN_PASSWORD" not in env and "SMTP_HOST" not in env

    kept = _render(CAREER_MARKED, values={}, existing={"SMTP_HOST": "smtp.icc.sk"}, preserved={"SMTP_PASSWORD": "old"})
    assert (kept["SMTP_HOST"], kept["SMTP_PASSWORD"]) == ("smtp.icc.sk", "old"), "what the installation has stays"

    given = _render(CAREER_MARKED, values={"SMTP_PASSWORD": "new"}, preserved={"SMTP_PASSWORD": "old"})
    assert given["SMTP_PASSWORD"] == "new", "what the manager entered wins"


def test_the_whole_render_writes_them_where_the_route_points(tmp_path):
    projects = tmp_path / "projects"
    (projects / "career-asistent").mkdir(parents=True)
    (projects / "career-asistent" / ".env.example").write_text(CAREER_MARKED, encoding="utf-8")
    (projects / "career-asistent" / "docker-compose.yml").write_text(
        "services:\n  db: {image: 'postgres:16-alpine'}\n  backend: {build: {context: ./backend}}\n"
        "  frontend: {build: {context: ./frontend}}\n",
        encoding="utf-8",
    )

    result = uat_provisioner.provision_uat(
        "career-asistent",
        "icc-uat",
        version="0.1.0",
        projects_root=projects,
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="career-asistent",
        full_project_slug="career-asistent",
        customer_values={"BOOTSTRAP_ADMIN_EMAIL": "spravca@icc.sk", "BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1"},
    )

    env = (tmp_path / "uat" / "icc" / "career-asistent" / ".env").read_text(encoding="utf-8").splitlines()
    compose = (tmp_path / "uat" / "icc" / "career-asistent" / "docker-compose.yml").read_text(encoding="utf-8")
    url = next(line.split("=", 1)[1] for line in env if line.startswith("APP_PUBLIC_URL="))
    assert "BOOTSTRAP_ADMIN_EMAIL=spravca@icc.sk" in env and "BOOTSTRAP_ADMIN_PASSWORD=Heslo-1" in env
    assert url == "https://uat-icc-career-asistent.isnex.eu"
    assert f"Host(`{url.removeprefix('https://')}`)" in compose, "the app's address is the one Traefik routes"
    assert result is not None


# --- the deploy ------------------------------------------------------------------------------------------------------


class _RecordingRunner(_FakeRunner):
    """A runner written for DEV-72: it takes the customer's values."""

    async def __call__(self, *, customer_values=None, **kwargs):
        self.customer_values = customer_values
        return await super().__call__(**kwargs)


def _career(db_session, monkeypatch, tmp_path, env_example: str):
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(settings, "credentials_storage_path", str(tmp_path / "creds"))
    (tmp_path / "career-asistent").mkdir()
    (tmp_path / "career-asistent" / ".env.example").write_text(env_example, encoding="utf-8")
    user = _make_user(db_session)
    project = _make_project(db_session, user=user, slug="career-asistent", auth_mode="password")
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project, slug="icc", subdomain="icc")
    return user, project, customer


@pytest.mark.asyncio
async def test_the_deploy_of_12_10_stops_before_anything_changes(db_session, monkeypatch, tmp_path):
    user, _project, customer = _career(db_session, monkeypatch, tmp_path, CAREER_1210)
    runner = _RecordingRunner()

    with pytest.raises(ValueError) as stopped:
        await _deploy(db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=runner)

    assert str(stopped.value).startswith("Nasadenie sa nedá spustiť: Heslo správcu aplikácie by dostalo náhodnú")
    assert runner.calls == [], "the installation was touched"


@pytest.mark.asyncio
async def test_a_marked_app_deploys_with_what_the_manager_entered(db_session, monkeypatch, tmp_path):
    user, _project, customer = _career(db_session, monkeypatch, tmp_path, CAREER_MARKED)
    customer_service.update(
        db_session,
        customer.id,
        CustomerUpdate(
            settings={"BOOTSTRAP_ADMIN_EMAIL": "spravca@icc.sk"},
            secret_settings={"BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1"},
        ),
    )
    runner = _RecordingRunner()

    event, _url, _bumped = await _deploy(
        db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=runner
    )

    assert event.status == "ok"
    assert runner.customer_values == {"BOOTSTRAP_ADMIN_EMAIL": "spravca@icc.sk", "BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1"}


@pytest.mark.asyncio
async def test_a_missing_required_setting_stops_the_deploy_by_name(db_session, monkeypatch, tmp_path):
    user, _project, customer = _career(db_session, monkeypatch, tmp_path, CAREER_MARKED)
    runner = _RecordingRunner()

    with pytest.raises(ValueError) as stopped:
        await _deploy(db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=runner)

    assert str(stopped.value) == (
        "Nasadenie sa nedá spustiť: Zákazníkovi chýbajú povinné nastavenia: E-mail prvého správcu — ním sa prvý raz "
        "prihlásiš; Heslo prvého správcu. Doplň ich v sekcii Zákazníci."
    )
    assert runner.calls == []


def test_the_default_runners_take_the_values():
    """Both runners the cockpit really uses pass them on to the render."""
    import inspect

    from backend.services import instance_adoption

    for runner in (deploy_service._default_deploy_runner, instance_adoption.adopting_deploy_runner):
        source = inspect.getsource(runner)
        assert "customer_values: Optional[dict[str, str]] = None" in source
        assert "customer_values=customer_values" in source


# --- the customer ----------------------------------------------------------------------------------------------------


def test_the_secrets_live_in_the_credentials_store_and_only_their_names_come_back(db_session, monkeypatch, tmp_path):
    _user, project, _c = _career(db_session, monkeypatch, tmp_path, CAREER_MARKED)

    customer = customer_service.create(
        db_session,
        project.id,
        CustomerCreate(
            name="ICC s.r.o.",
            slug="icc2",
            settings={"BOOTSTRAP_ADMIN_EMAIL": "spravca@icc.sk", "SMTP_HOST": ""},
            secret_settings={"BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1"},
        ),
    )

    assert customer.settings == {"BOOTSTRAP_ADMIN_EMAIL": "spravca@icc.sk"}, "an empty value is no setting"
    assert "Heslo-1" not in json.dumps(customer.settings)
    assert customer_service.secret_setting_keys(db_session, customer) == ["BOOTSTRAP_ADMIN_PASSWORD"]
    stored = list(Path(settings.credentials_storage_path).glob("*-settings.md"))
    assert len(stored) == 1 and json.loads(stored[0].read_text(encoding="utf-8")) == {
        "BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1"
    }

    customer_service.update(db_session, customer.id, CustomerUpdate(secret_settings={"SMTP_PASSWORD": "mail"}))
    assert customer_service.read_secret_settings(db_session, customer) == {
        "BOOTSTRAP_ADMIN_PASSWORD": "Heslo-1",
        "SMTP_PASSWORD": "mail",
    }, "a secret not sent again stays"


@pytest.mark.parametrize(
    ("plain", "secret"),
    [({"DATABASE_URL": "x"}, {}), ({"BOOTSTRAP_ADMIN_PASSWORD": "x"}, {}), ({}, {"BOOTSTRAP_ADMIN_EMAIL": "x"})],
)
def test_only_what_the_app_declares_and_of_its_kind_can_be_stored(db_session, monkeypatch, tmp_path, plain, secret):
    _user, project, _c = _career(db_session, monkeypatch, tmp_path, CAREER_MARKED)

    with pytest.raises(ValueError) as refused:
        customer_service.create(
            db_session, project.id, CustomerCreate(name="X", slug="x", settings=plain, secret_settings=secret)
        )

    assert str(refused.value).startswith("Aplikácia tieto nastavenia zákazníka nedeklaruje")


def test_the_charter_tells_the_agent_the_contract():
    charter = (Path(claude_agent.__file__).resolve().parents[2] / "templates" / "ai-agent-charter.md").read_text(
        encoding="utf-8"
    )
    assert "**Nastavenia zákazníka a prvý správca — značky v `.env.example` (DEV-72).**" in charter
    for marker in ("# @customer:", "# @customer-secret:", "# @customer required:", "# @public-url"):
        assert marker in charter
    # The charter's examples must be markers the cockpit really reads.
    example = "# @customer required: „E-mail“\nBOOTSTRAP_ADMIN_EMAIL=\n# @public-url\nAPP_PUBLIC_URL=\n"
    assert [(s.kind, s.required) for s in env_contract.parse(example).settings] == [
        ("customer", True),
        ("public_url", False),
    ]
