"""Regression guard for the KB-ghost coverage gap (docs/specs/kb-ghost-followup.md Fix A).

Background: ``_isolate_create_project_kb`` was opt-in via ``pytestmark`` on only two
modules. ``test_auth_flow.py::test_login_then_create_project`` creates the slug
``test-auth-project`` — EXACTLY one of the ghost names hand-cleaned from the real KB
on 2026-06-13 + 2026-07-09 — on the create SUCCESS path with NO isolation and NO
init.sh dry-run, so the ghost recurred whenever ``template_init_script_path`` was
configured. Fix A makes the fixture autouse for the WHOLE integration suite (see
``tests/integration/conftest.py::_auto_isolate_create_project_kb``).

These tests prove the fix three ways:

* :class:`TestIsolationActiveForEveryIntegrationTest` — inspection-only assertions
  that the autouse fixture redirected the three ghost vectors for a plain integration
  test that never asked for it (the exact coverage ``test_auth_flow`` now inherits).
  RED before Fix A (a plain integration test saw the real KB), GREEN after.
* :func:`test_auth_flow_create_leak_probe_is_redirected_to_tmp` — drives the real
  login→create ``test-auth-project`` flow with a fake init.sh that WOULD write a KB
  ghost, and proves the write lands in the tmp KB, never the real one.
* :func:`test_known_ghost_slugs_absent_from_real_kb` — a first-class assertion that
  the hand-cleaned ghost names are absent from the real KB after the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.api.routes.projects as projects_route
from backend.config.settings import settings
from backend.services import template_bootstrap

# The real shared KB — the thing no test may ever write to.
REAL_KB = Path("/home/icc/knowledge")
REAL_KB_PROJECTS = REAL_KB / "projects"

# Ghost slugs hand-cleaned 2026-06-13 + 2026-07-09 (docs/specs/kb-ghost-root-cause.md
# acceptance list). ``test-auth-project`` is the Fix-A smoking gun from test_auth_flow.
GHOST_SLUGS = (
    "test-auth-project",
    "dup-slug-test",
    "bad-repo-proj",
    "boundary-min",
    "boundary-max",
    "structure-test",
)


@pytest.mark.integration
class TestIsolationActiveForEveryIntegrationTest:
    """The autouse fixture redirects all three ghost vectors for EVERY integration
    test — even one that never requests it (the coverage test_auth_flow now gets).

    Inspection-only (no filesystem writes), so it is the safe RED→GREEN discriminator:
    before Fix A a plain integration test saw the real KB / raw init.sh entrypoint.
    """

    def test_settings_kb_path_redirected_away_from_real_kb(self):
        # Vector 1: settings.knowledge_base_path (read by get_knowledge_base_writer
        # at call time) points at a tmp dir, not /home/icc/knowledge.
        assert Path(settings.knowledge_base_path).resolve() != REAL_KB.resolve()
        assert "/home/icc/knowledge" not in settings.knowledge_base_path

    def test_init_sh_vector_forced_to_dry_run(self):
        # Vector 3: the route's invoke_init_script is the dry-run wrapper, not the
        # raw subprocess entrypoint that historically wrote /opt/projects + the KB.
        assert projects_route.invoke_init_script is not template_bootstrap.invoke_init_script


@pytest.mark.integration
def test_auth_flow_create_leak_probe_is_redirected_to_tmp(
    integration_client, _seed_admin, _isolate_create_project_kb, monkeypatch
):
    """End-to-end proof: the exact test_auth_flow create (login → create
    ``test-auth-project``) is KB-isolated. We install a fake init.sh that WOULD
    write a KB ghost (mirroring what the real template does to
    ``{knowledge_base_path}/projects/<slug>/``); the isolation must redirect that
    write into the tmp KB so the real KB stays clean.

    Requesting ``_isolate_create_project_kb`` explicitly makes this test safe by
    construction — the redirection is always active here, so the fake write can
    never reach the real KB even while it demonstrates the leak vector.
    """
    kb_root = _isolate_create_project_kb

    def _fake_init_sh(db, project, **_kwargs):
        # Emulate init.sh's KB side-effect against the CURRENT knowledge_base_path
        # (redirected to the tmp KB by the isolation fixture).
        ghost_dir = Path(settings.knowledge_base_path) / "projects" / project.slug
        ghost_dir.mkdir(parents=True, exist_ok=True)
        (ghost_dir / "GHOST.md").write_text("# would-be ghost\n", encoding="utf-8")
        return None

    # Overrides the fixture's dry-run wrapper for this test (same monkeypatch).
    monkeypatch.setattr("backend.api.routes.projects.invoke_init_script", _fake_init_sh)

    login = integration_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Nex123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    user_id = login.json()["user"]["id"]

    resp = integration_client.post(
        "/api/v1/projects",
        json={
            "name": "Test Auth Project",
            "slug": "test-auth-project",
            "type": "standard",
            "auth_mode": "password",
            "description": "Created during KB-ghost regression test",
            "created_by": user_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    # The would-be ghost landed in the tmp KB (the fake ran)...
    assert (kb_root / "projects" / "test-auth-project" / "GHOST.md").exists()
    # ...and NEVER in the real shared KB.
    assert not (REAL_KB_PROJECTS / "test-auth-project").exists()


@pytest.mark.integration
def test_known_ghost_slugs_absent_from_real_kb():
    """First-class acceptance (docs/specs/kb-ghost-followup.md): the hand-cleaned
    ghost slugs must be absent from the real KB. Complements the per-test sentinel
    in ``_isolate_create_project_kb`` (which catches any NEW leak this run)."""
    if not REAL_KB_PROJECTS.is_dir():
        pytest.skip(f"real KB projects dir not present in this environment: {REAL_KB_PROJECTS}")

    present = {p.name for p in REAL_KB_PROJECTS.iterdir()}
    leaked = sorted(present & set(GHOST_SLUGS))
    assert not leaked, (
        f"ghost scaffold dirs present in the real KB {REAL_KB_PROJECTS}: {leaked} — "
        "a create-touching test leaked into the shared KB (KB isolation broke)."
    )
