"""DEV-69 — a NEX Manager and the modules it opens agree on the launch ticket, and the cockpit says when they do not.

11.10.2026 the UAT NEX Manager of MAGERSTAV signed its tickets with the installation name „local“ (its template's
default) while the UAT NEX Inbox accepted only „uat-mager“ — the name both had before the cockpit took them over.
Every launch from the Manager ended in NIB-081. The deploy door check (ICCINT-69) never said so: it minted its test
ticket from the module's own settings, i.e. from the values the module checks against.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jose import jwt

from backend.services import deploy as deploy_service
from backend.services import launch_pairing, uat_launch, uat_provisioner
from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer, _make_project, _make_user, _make_version

KEY = "launch-signing-key-0123456789abcdef"

#: The pair as it stood on 11.10.2026 (key equal, installation names not).
MANAGER_1110 = {"LAUNCH_SIGNING_KEY": KEY, "DEPLOY_SLUG": "local"}
INBOX_1110 = {"MANAGER_LAUNCH_SIGNING_KEY": KEY, "MANAGER_DEPLOY_SLUG": "uat-mager", "MANAGER_MODULE_SLUG": "nex-inbox"}


def _write_env(folder: Path, env: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")


# --- the comparison ---------------------------------------------------------------------------------------------


def test_the_pair_of_11_10_is_told_apart_by_its_installation_name():
    assert launch_pairing.mismatches(MANAGER_1110, INBOX_1110, "nex-inbox") == [
        "NEX Manager podpisuje menom inštalácie „local“, modul prijíma len „uat-mager“"
    ]
    agreeing = {**MANAGER_1110, "DEPLOY_SLUG": "uat-mager"}
    assert launch_pairing.mismatches(agreeing, INBOX_1110, "nex-inbox") == []


def test_every_part_of_the_ticket_is_compared_and_no_key_is_shown():
    other_key = {**MANAGER_1110, "DEPLOY_SLUG": "uat-mager", "LAUNCH_SIGNING_KEY": "another-key-0123456789"}
    found = launch_pairing.mismatches(other_key, INBOX_1110, "inbox")

    assert found == [
        "podpisový kľúč modulu sa nezhoduje s kľúčom NEX Managera",
        "NEX Manager otvára modul ako „inbox“, modul čaká „nex-inbox“",
    ]
    assert not any(KEY in part or "another-key" in part for part in found)
    assert launch_pairing.mismatches({"DEPLOY_SLUG": "uat-mager"}, INBOX_1110, "nex-inbox") == [
        "NEX Manager nemá podpisový kľúč, takže lístok nepodpíše"
    ]


def test_a_module_without_an_installation_name_does_not_check_it():
    """NEX Inbox: an empty ``MANAGER_DEPLOY_SLUG`` switches the check off — no mismatch to report."""
    module = {**INBOX_1110, "MANAGER_DEPLOY_SLUG": ""}
    assert launch_pairing.mismatches(MANAGER_1110, module, "nex-inbox") == []


def test_the_customer_warning_names_only_modules_opened_from_the_manager():
    modules = {"nex-inbox": INBOX_1110, "nex-websites": {"SOMETHING": "else"}}

    assert launch_pairing.customer_warning(MANAGER_1110, modules) == (
        "Z NEX Managera sa nedá otvoriť: nex-inbox: NEX Manager podpisuje menom inštalácie „local“, modul prijíma "
        "len „uat-mager“. Aplikácie bežia — pokazené je otvorenie z NEX Managera; nastavenia treba zladiť (urobí to "
        "technický tím)."
    )
    assert launch_pairing.customer_warning({**MANAGER_1110, "DEPLOY_SLUG": "uat-mager"}, modules) is None


# --- the door check signs as the Manager ------------------------------------------------------------------------


class _InboxDoor:
    """A module that checks a ticket like NEX Inbox does (``verify_manager_launch_token``)."""

    def __init__(self, module_env: dict[str, str]):
        self.env, self.tickets = module_env, []

    def __call__(self, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, data=None, **_kwargs):
        self.tickets.append(data["lt"])
        try:
            claims = jwt.decode(
                data["lt"],
                self.env["MANAGER_LAUNCH_SIGNING_KEY"],
                algorithms=["HS256"],
                audience=self.env["MANAGER_MODULE_SLUG"],
            )
            accepted = claims.get("deploy") == self.env["MANAGER_DEPLOY_SLUG"]
        except Exception:  # noqa: BLE001 — whatever the module rejects
            accepted = False
        return _Reply(303 if accepted else 401, {"session": "x"} if accepted else {})

    def get(self, url, cookies=None):
        return _Reply(200)


class _Reply:
    def __init__(self, status_code: int, cookies=None):
        self.status_code, self.cookies = status_code, cookies or {}


def _uat(monkeypatch, tmp_path, manager_env, module_env) -> _InboxDoor:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    if manager_env is not None:
        _write_env(tmp_path / "mager" / "nex-manager", manager_env)
    _write_env(tmp_path / "mager" / "nex-inbox", module_env)
    door = _InboxDoor(module_env)
    monkeypatch.setattr(uat_launch.httpx, "Client", door)
    return door


def test_the_door_check_now_catches_the_pair_of_11_10(tmp_path, monkeypatch):
    door = _uat(monkeypatch, tmp_path, MANAGER_1110, INBOX_1110)

    opens, detail = uat_launch.uat_door_opens("mager", "nex-inbox", "https://uat-mager-inbox.test", subject="zoltan")

    assert opens is False, "the door check let through a ticket the module rejects"
    assert detail == "aplikácia vstupenku odmietla novým tvarom (v tele) (stav 401)"
    claims = jwt.get_unverified_claims(door.tickets[0])
    assert (claims["deploy"], claims["aud"]) == ("local", "nex-inbox"), "the ticket was not signed as the Manager"


def test_an_agreeing_pair_opens(tmp_path, monkeypatch):
    _uat(monkeypatch, tmp_path, {**MANAGER_1110, "DEPLOY_SLUG": "uat-mager"}, INBOX_1110)

    opens, _detail = uat_launch.uat_door_opens("mager", "nex-inbox", "https://uat-mager-inbox.test", subject="zoltan")

    assert opens is True


def test_without_a_manager_there_is_nothing_to_sign_with(tmp_path, monkeypatch):
    door = _uat(monkeypatch, tmp_path, None, INBOX_1110)

    opens, detail = uat_launch.uat_door_opens("mager", "nex-inbox", "https://uat-mager-inbox.test", subject="zoltan")

    assert (opens, door.tickets) == (None, [])
    assert detail == "vstupenka sa nedá vyraziť (zákazník nemá NEX Manager s podpisovým kľúčom)"


# --- after a deploy -------------------------------------------------------------------------------------------------


def test_the_pairing_is_read_where_the_installations_run(tmp_path, monkeypatch):
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "mager" / "nex-manager", MANAGER_1110)
    _write_env(tmp_path / "mager" / "nex-inbox", INBOX_1110)

    warning = deploy_service._launch_pairing_warning("mager", "uat", None, ["nex-inbox"])

    assert warning is not None and "„local“, modul prijíma len „uat-mager“" in warning
    assert deploy_service._launch_pairing_warning("andros", "uat", None, ["nex-inbox"]) is None, "no Manager there"


@pytest.mark.asyncio
@pytest.mark.parametrize("deployed", ["module", "manager"])
async def test_a_deploy_of_the_module_or_of_the_manager_says_it(db_session, tmp_path, monkeypatch, deployed):
    user = _make_user(db_session)
    inbox = _make_project(db_session, user=user, slug="nex-inbox", auth_mode="token")
    manager = _make_project(db_session, user=user, slug="nex-manager", auth_mode="password")
    project = inbox if deployed == "module" else manager
    _make_version(db_session, project, "0.1.0")
    inbox_customer = _make_customer(db_session, inbox, slug="magerstav", subdomain="mager")
    # both installations live under the same customer folder — the subdomain decides it
    manager_customer = _make_customer(db_session, manager, slug="MAGERSTAV", subdomain="mager")
    customer = inbox_customer if deployed == "module" else manager_customer
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "mager" / "nex-manager", MANAGER_1110)
    _write_env(tmp_path / "mager" / "nex-inbox", INBOX_1110)
    monkeypatch.setattr(deploy_service, "_uat_door_warning", lambda *a, **k: None)

    event, _url, _bumped = await _deploy(
        db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=_FakeRunner()
    )

    assert event.status == "ok", "a pairing problem never fails a deploy — the apps run"
    said = "Z NEX Managera sa nedá otvoriť: nex-inbox: NEX Manager podpisuje menom inštalácie „local“"
    assert [w for w in event.warnings if said in w], "the audit row must keep the pairing warning"


# --- a render from scratch keeps what the installation is ------------------------------------------------------------


def test_a_render_from_scratch_keeps_the_installations_own_values():
    kept: list[str] = []
    env = uat_provisioner.generate_uat_env(
        slug="uat-mager",
        project="nex-manager",
        version="1.2.3",
        services={},
        be_service=None,
        db_service=None,
        source_env_example={"DEPLOY_SLUG": "local", "APP_VERSION": "1.0.0", "GENESIS_URL": ""},  # blank = fill in
        db_user="u",
        db_name="d",
        shared_db_password="p",
        existing_env={"DEPLOY_SLUG": "uat-mager", "APP_VERSION": "1.2.2", "GENESIS_URL": "http://genesis.local"},
        kept_identity=kept,
    )
    lines = env.splitlines()

    assert "DEPLOY_SLUG=uat-mager" in lines
    assert "GENESIS_URL=http://genesis.local" in lines
    assert "APP_VERSION=1.2.3" in lines, "the version moves with the deploy"
    assert sorted(kept) == ["DEPLOY_SLUG", "GENESIS_URL"]


def test_a_secrets_rotation_says_which_values_it_kept(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "nex-manager").mkdir(parents=True)
    source = {
        "services": {
            "db": {"image": "postgres:16-alpine"},
            "backend": {"build": {"context": "./backend"}, "image": "nex-manager-backend:v1.2.3"},
        }
    }
    (projects / "nex-manager" / "docker-compose.yml").write_text(yaml.safe_dump(source), encoding="utf-8")
    (projects / "nex-manager" / ".env.example").write_text("DEPLOY_SLUG=local\n", encoding="utf-8")
    uat_root = tmp_path / "uat"
    installation = uat_root / "mager" / "nex-manager"
    _write_env(installation, {"DEPLOY_SLUG": "uat-mager", "POSTGRES_PASSWORD": "old"})
    (installation / "docker-compose.yml").write_text(f"# {uat_provisioner.GENERATED_BY_MARKER}\nservices: {{}}\n")

    result = uat_provisioner.provision_uat(
        "nex-manager",
        "mager-uat",
        version="1.2.3",
        rotate_secrets=True,
        projects_root=projects,
        uat_root=uat_root,
        customer_slug="mager",
        app="nex-manager",
        full_project_slug="nex-manager",
    )

    assert "DEPLOY_SLUG=uat-mager" in (installation / ".env").read_text(encoding="utf-8").splitlines()
    assert (
        "Inštalácia si ponechala vlastné nastavenia, ktoré vzor projektu predpisuje inak: DEPLOY_SLUG — kokpit ich "
        "pri vykreslení nanovo neprepísal."
    ) in result.warnings
