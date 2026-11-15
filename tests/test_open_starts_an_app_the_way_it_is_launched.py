"""DEV-391 — „Otvoriť“ on the Inštalácie page opens an app the way the app is really started.

15.11.2026 the Director: „V novej záložke Inštalácie sú tlačidlá „Otvoriť“, ale nefungujú u tých aplikácií, ktoré sa
spúšťajú z NEX Manager s tokenom.“ The button was a plain link, and a token app lets nobody in without the Manager's
ticket. On the proposal — in UAT signed in as „Spustiť“ on the UAT page, in PROD from the customer's NEX Manager:
„Súhlasím“.
"""

from __future__ import annotations

import uuid

from backend.core.authz import ADMIN_USERNAME
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import installations as I
from backend.services import launch_pairing
from tests.test_the_installations_page_shows_every_installation import _customer, _deployed, _project, _user


def _app(groups, slug, customer_name):
    group = next(g for g in groups if g["name"] == customer_name)
    return next(a for a in group["apps"] if a["project_slug"] == slug)


def _world(db):
    admin = db.query(User).filter_by(username=ADMIN_USERNAME).one_or_none() or _user(db, ADMIN_USERNAME)
    manager = db.query(Project).filter_by(slug=launch_pairing.MANAGER_PROJECT).one_or_none() or _project(
        db, admin, launch_pairing.MANAGER_PROJECT, "NEX Manager"
    )
    inbox = _project(db, admin, f"nex-inbox-{uuid.uuid4().hex[:4]}", "NEX Inbox")
    inbox.auth_mode = "token"
    websites = _project(db, admin, f"nex-websites-{uuid.uuid4().hex[:4]}", "NEX Websites")  # signs in by password
    db.flush()
    _deployed(db, _customer(db, manager, "ICC s.r.o.", "icc"), "prod", "1.6.0")
    _deployed(db, _customer(db, manager, "MÁGERSTAV s.r.o.", "mager"), "uat", "1.6.0")  # no PROD Manager
    for customer in (_customer(db, inbox, "ICC s.r.o.", "icc"), _customer(db, inbox, "MÁGERSTAV s.r.o.", "mager")):
        _deployed(db, customer, "uat", "1.11.0")
        _deployed(db, customer, "prod", "1.10.3")
    icc_websites = _customer(db, websites, "ICC s.r.o.", "icc")
    _deployed(db, icc_websites, "uat", "1.2.0")
    _deployed(db, icc_websites, "prod", "1.2.0")
    return admin, inbox, websites


def test_a_token_app_opens_signed_in_in_uat_and_from_the_customers_manager_in_prod(db_session):
    admin, inbox, _websites = _world(db_session)

    app = _app(I.list_installations(db_session, admin), inbox.slug, "ICC s.r.o.")

    assert (app["uat"]["opens"], app["uat"]["manager_url"]) == ("uat_launch", None)
    assert (app["prod"]["opens"], app["prod"]["manager_url"]) == ("manager", "https://icc-manager.isnex.eu")


def test_without_a_live_manager_the_live_token_app_says_where_it_opens_but_links_nowhere(db_session):
    admin, inbox, _websites = _world(db_session)

    app = _app(I.list_installations(db_session, admin), inbox.slug, "MÁGERSTAV s.r.o.")

    assert (app["prod"]["opens"], app["prod"]["manager_url"]) == ("manager", None)


def test_an_app_that_signs_in_by_password_keeps_its_link(db_session):
    admin, _inbox, websites = _world(db_session)

    app = _app(I.list_installations(db_session, admin), websites.slug, "ICC s.r.o.")

    assert [(app[env]["opens"], app[env]["manager_url"]) for env in ("uat", "prod")] == [("link", None), ("link", None)]
