"""Overwrite guard: the provisioner must never render over a deployment it did not generate.

``/opt/uat`` and ``/opt/customers`` are shared ground. Besides the instances this module renders, they
hold hand-authored, LIVE customer stacks at paths ``provision_uat`` can address with nothing but a
customer slug and a project slug — ``/opt/customers/mager/nex-inbox`` (mager-inbox.isnex.eu),
``/opt/customers/mager|icc|andros/nex-manager``, ``/opt/uat/inbox`` (the ostrý MÁGERSTAV inbox UAT),
``/opt/uat/mager|icc|andros/nex-manager``, ``/opt/customers/dev/nex-studio*``. A render there replaces
that customer's ``docker-compose.yml`` — which IS their public Traefik routing (the andros-payables
outage of 2026-07-10) — and their ``.env``.

So provenance is proved before writing: only a ``docker-compose.yml`` carrying the generated-by header
of ``templates/uat/docker-compose.yml.j2`` marks a directory as ours to rewrite. Anything else is
refused with :class:`HandAuthoredDeploymentError` and left byte-for-byte untouched. These tests use the
REAL header lines of the live stacks above (comments, no secrets) so the guard is exercised against the
exact shapes on this host, and they pin the two ways the guard could rot: the template silently losing
the marker (→ every redeploy refuses) and the marker becoming forgeable from a service body.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from backend.services import uat_provisioner as P

SOURCE_COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: appuser
          POSTGRES_DB: appdb
          POSTGRES_PASSWORD: ${DB_PASSWORD}
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        ports: ["8000:8000"]
      frontend:
        build: ./frontend
        ports: ["3000:80"]
    """
)

# First lines of REAL hand-authored deployments on this host — the shapes the guard has to recognise as
# "not mine". Verbatim comments; they carry no credentials.
LIVE_MAGER_INBOX_PROD = (
    "# NEX Inbox v1.4.0 — PROD pre MÁGERSTAV (mager-inbox.isnex.eu)\n"
    "# Per-customer PROD (ICC deploy naming standard). PINNED launch-capable image v1.4.0.\n"
)
LIVE_MAGER_MANAGER_PROD = (
    "# NEX Manager v1.0.0 — PROD pre MÁGERSTAV (mager-manager.isnex.eu)\n"
    "# Per-customer PROD (ICC deploy naming standard). PINNED images (byte-identické s UAT v1.0.0),\n"
)
LIVE_INBOX_UAT_TEMPLATE = (
    "# UAT docker-compose template — per F-003 §3 + CR-022 amendment.\n"
    "#\n"
    "# Generic 3-service stack (postgres + backend + frontend) renderovaný cez\n"
)
LIVE_STUDIO_DEV_PROD = "# NEX Studio v3.0.0 — NEW GENERATION production instance (conversation-first AI partner).\n"

LIVE_BODY = "name: mager-inbox\nservices:\n  backend:\n    image: nex-inbox-backend:1.4.0\n"
LIVE_ENV = "DB_PASSWORD=the-customers-real-password\nSMTP_HOST=mail.example.test\n"


def _make_source(tmp_path: Path, slug: str = "nex-inbox") -> Path:
    project = tmp_path / "projects" / slug
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE_COMPOSE, encoding="utf-8")
    return project


def _plant_live_deployment(target: Path, header: str = LIVE_MAGER_INBOX_PROD) -> tuple[bytes, bytes]:
    """Put a hand-authored compose + ``.env`` at ``target``; return their exact bytes for a later diff."""
    target.mkdir(parents=True)
    (target / "docker-compose.yml").write_text(header + LIVE_BODY, encoding="utf-8")
    (target / ".env").write_text(LIVE_ENV, encoding="utf-8")
    return (target / "docker-compose.yml").read_bytes(), (target / ".env").read_bytes()


def _provision_prod(tmp_path: Path, **kw):
    """A PROD deploy of nex-inbox for customer ``mager`` → ``<prod_root>/mager/nex-inbox`` (the live path)."""
    return P.provision_uat(
        "nex-inbox",
        "mager-prod",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug="mager",
        app="inbox",
        full_project_slug="nex-inbox",
        **kw,
    )


# ---------- the refusal ----------


def test_prod_refuses_to_overwrite_a_live_customer_deployment(tmp_path):
    """``/opt/customers/mager/nex-inbox`` shape: a PROD render is refused, both files survive verbatim."""
    _make_source(tmp_path)
    target = tmp_path / "customers" / "mager" / "nex-inbox"
    compose_before, env_before = _plant_live_deployment(target)

    with pytest.raises(P.HandAuthoredDeploymentError):
        _provision_prod(tmp_path)

    assert (target / "docker-compose.yml").read_bytes() == compose_before
    assert (target / ".env").read_bytes() == env_before
    # Nothing was created alongside them either — the refusal happens before the first mkdir.
    assert not (target / "snapshots").exists()
    assert not (target / "logs").exists()


def test_flat_uat_refuses_to_overwrite_the_live_inbox_uat(tmp_path):
    """``/opt/uat/inbox`` shape (the ostrý MÁGERSTAV inbox UAT): the project-level path is guarded too."""
    _make_source(tmp_path)
    target = tmp_path / "uat" / "inbox"
    compose_before, env_before = _plant_live_deployment(target, LIVE_INBOX_UAT_TEMPLATE)

    with pytest.raises(P.HandAuthoredDeploymentError):
        P.provision_uat(
            "nex-inbox",
            "inbox",
            projects_root=tmp_path / "projects",
            uat_root=tmp_path / "uat",
        )

    assert (target / "docker-compose.yml").read_bytes() == compose_before
    assert (target / ".env").read_bytes() == env_before


def test_per_customer_uat_refuses_to_overwrite_a_live_manager_uat(tmp_path):
    """``/opt/uat/mager/nex-manager`` shape: the per-customer UAT path is guarded exactly like PROD."""
    _make_source(tmp_path, "nex-manager")
    target = tmp_path / "uat" / "mager" / "nex-manager"
    compose_before, _ = _plant_live_deployment(target, LIVE_MAGER_MANAGER_PROD)

    with pytest.raises(P.HandAuthoredDeploymentError):
        P.provision_uat(
            "nex-manager",
            "mager-uat",
            projects_root=tmp_path / "projects",
            uat_root=tmp_path / "uat",
            prod_root=tmp_path / "customers",
            customer_slug="mager",
            app="manager",
            full_project_slug="nex-manager",
        )

    assert (target / "docker-compose.yml").read_bytes() == compose_before


@pytest.mark.parametrize(
    "header",
    [LIVE_MAGER_INBOX_PROD, LIVE_MAGER_MANAGER_PROD, LIVE_INBOX_UAT_TEMPLATE, LIVE_STUDIO_DEV_PROD],
)
def test_no_live_header_is_mistaken_for_generated(tmp_path, header):
    (tmp_path / "docker-compose.yml").write_text(header + LIVE_BODY, encoding="utf-8")
    assert P.is_provisioner_generated(tmp_path / "docker-compose.yml") is False


def test_refusal_message_names_the_path_the_file_and_that_nothing_changed(tmp_path):
    """What the manager reads has to answer: what is there, what happened to it, what to do now."""
    _make_source(tmp_path)
    _plant_live_deployment(tmp_path / "customers" / "mager" / "nex-inbox")

    with pytest.raises(P.HandAuthoredDeploymentError) as excinfo:
        _provision_prod(tmp_path)

    message = str(excinfo.value)
    assert str(tmp_path / "customers" / "mager" / "nex-inbox") in message  # WHICH path
    assert "docker-compose.yml" in message  # WHAT proved it foreign
    assert "neprepísal" in message and ".env" in message  # nothing was touched
    assert "allow_overwrite" in message  # the one way forward, named
    # The refusal reports on the file, never FROM it — no line of the customer's compose/.env leaks.
    assert "the-customers-real-password" not in message
    assert "nex-inbox-backend:1.4.0" not in message


def test_refusal_is_a_valueerror_so_existing_callers_report_it_cleanly(tmp_path):
    """The cockpit runner catches ``(FileNotFoundError, ValueError)`` → a clean 'provision failed: …',
    not a 500. The named subclass stays catchable on its own for a caller that offers the override."""
    assert issubclass(P.HandAuthoredDeploymentError, ValueError)
    _make_source(tmp_path)
    _plant_live_deployment(tmp_path / "customers" / "mager" / "nex-inbox")
    with pytest.raises(ValueError):
        _provision_prod(tmp_path)


# ---------- what must still go through ----------


def test_redeploy_of_our_own_instance_is_untouched_by_the_guard(tmp_path):
    """The ordinary redeploy: the first render leaves the marker, so the second one is allowed."""
    _make_source(tmp_path)
    first = _provision_prod(tmp_path)
    assert P.is_provisioner_generated(first.compose_path) is True

    second = _provision_prod(tmp_path)
    assert second.is_redeploy is True
    assert second.compose_path == first.compose_path


def test_fresh_target_directory_provisions(tmp_path):
    _make_source(tmp_path)
    result = _provision_prod(tmp_path)
    assert result.compose_path.is_file() and result.env_path.is_file()


def test_existing_but_empty_target_directory_provisions(tmp_path):
    """A leftover directory (an earlier run that died before writing) holds nothing to destroy."""
    _make_source(tmp_path)
    (tmp_path / "customers" / "mager" / "nex-inbox").mkdir(parents=True)
    result = _provision_prod(tmp_path)
    assert result.compose_path.is_file()


def test_env_without_a_compose_is_refused(tmp_path):
    """Provenance unprovable + an ``.env`` whose hand-set values exist nowhere else → refuse, not guess."""
    _make_source(tmp_path)
    target = tmp_path / "customers" / "mager" / "nex-inbox"
    target.mkdir(parents=True)
    (target / ".env").write_text(LIVE_ENV, encoding="utf-8")

    with pytest.raises(P.HandAuthoredDeploymentError, match=r"\.env"):
        _provision_prod(tmp_path)

    assert (target / ".env").read_text(encoding="utf-8") == LIVE_ENV


# ---------- the override is explicit, and only explicit ----------


def test_overwrite_never_happens_by_default():
    """Default-off is the whole point: no call in the chain may reach the write without asking for it."""
    import inspect

    assert inspect.signature(P.provision_uat).parameters["allow_overwrite"].default is False


def test_explicit_allow_overwrite_replaces_the_deployment(tmp_path):
    """With the deliberate flag the render proceeds — and the result is now OURS (carries the marker)."""
    _make_source(tmp_path)
    target = tmp_path / "customers" / "mager" / "nex-inbox"
    _plant_live_deployment(target)

    result = _provision_prod(tmp_path, allow_overwrite=True)

    assert P.is_provisioner_generated(result.compose_path) is True
    assert "nex-inbox-backend:1.4.0" not in result.compose_path.read_text(encoding="utf-8")


# ---------- provenance detection itself ----------


def test_rendered_compose_carries_the_marker_the_guard_looks_for(tmp_path):
    """Binds template ↔ guard: if ``templates/uat/docker-compose.yml.j2`` ever loses the generated-by
    line, this fails HERE instead of turning every future redeploy into a refusal."""
    rendered = P.render_uat_compose({"name": "uat-demo", "services": {"db": {"image": "postgres:16-alpine"}}})
    path = tmp_path / "docker-compose.yml"
    path.write_text(rendered, encoding="utf-8")
    assert P.GENERATED_BY_MARKER in rendered
    assert P.is_provisioner_generated(path) is True


def test_marker_below_the_header_does_not_count_as_provenance(tmp_path):
    """Only the leading comment block is provenance — a mid-file mention (a comment after the first
    service, a label, an env value quoting this module's path) must not unlock the overwrite."""
    path = tmp_path / "docker-compose.yml"
    path.write_text(
        LIVE_MAGER_INBOX_PROD
        + "name: mager-inbox\n"
        + "services:\n"
        + f"  # rendered like {P.GENERATED_BY_MARKER} but by hand\n"
        + "  backend:\n"
        + f"    environment:\n      NOTE: {P.GENERATED_BY_MARKER}\n",
        encoding="utf-8",
    )
    assert P.is_provisioner_generated(path) is False


def test_unreadable_or_absent_compose_is_not_ours(tmp_path):
    """Provenance must be proved. A directory (or a vanished file) where the compose should be proves
    nothing — and an unprovable path is somebody else's."""
    assert P.is_provisioner_generated(tmp_path / "nope" / "docker-compose.yml") is False
    (tmp_path / "docker-compose.yml").mkdir()
    assert P.is_provisioner_generated(tmp_path / "docker-compose.yml") is False
