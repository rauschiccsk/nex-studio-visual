"""Regression: the PROD (redeploy) Traefik Host rule must be the public vhost, NOT an extra_hosts value.

Incident 2026-07-10 (andros-payables PROD outage): ``build_uat_compose`` had a loop variable named ``host``
(``for host in extra_backend_hosts``) that clobbered the outer ``host`` (the public vhost from
``_instance_naming``) with the LAST extra_hosts entry (``host.docker.internal:host-gateway``). The Traefik
router rules were then built as ``Host(`host.docker.internal:host-gateway`)`` → the instance was publicly
unreachable (health probe down, critical alert). It only triggered on REDEPLOY (``extra_backend_hosts``
non-empty), so the first deploy looked fine. This pins the correct behaviour.
"""

from __future__ import annotations

from pathlib import Path

from backend.services.uat_provisioner import _instance_naming, build_uat_compose


def _label_strings(svc: dict) -> list[str]:
    labels = svc.get("labels") or []
    if isinstance(labels, dict):
        return [f"{k}={v}" for k, v in labels.items()]
    return [str(x) for x in labels]


def _build_prod_redeploy() -> dict:
    """A PROD redeploy whose existing backend carried an ``extra_hosts`` (the outage trigger)."""
    source = {
        "services": {
            "web": {"image": "app-web:latest", "ports": ["3000:80"]},
            "backend": {
                "image": "app-be:latest",
                "ports": ["8000:8000"],
                "extra_hosts": ["host.docker.internal:host-gateway"],
                "environment": {"POSTGRES_PASSWORD": "x"},
            },
            "db": {"image": "postgres:16", "environment": {"POSTGRES_PASSWORD": "x"}},
        }
    }
    return build_uat_compose(
        slug="andros-payables",
        project="nex-payables",
        project_path=Path("/opt/projects/nex-payables"),
        source=source,
        roles={"frontend": "web", "backend": "backend", "db": "db"},
        db_user="app",
        db_name="app",
        extra_backend_hosts=["host.docker.internal:host-gateway"],  # simulates the live instance's extra_hosts
        environment="prod",
        customer_slug="andros",
        app="payables",
    )


def test_prod_traefik_host_is_the_public_vhost_not_extra_hosts() -> None:
    result = _build_prod_redeploy()
    web_labels = _label_strings(result["services"]["web"])
    be_labels = _label_strings(result["services"]["backend"])

    # The FE + BE routers must route the clean public vhost.
    assert any("Host(`andros-payables.isnex.eu`)" in lbl for lbl in web_labels), web_labels
    assert any("Host(`andros-payables.isnex.eu`)" in lbl for lbl in be_labels), be_labels

    # And the extra_hosts value must NEVER leak into a Traefik Host() rule (the outage's exact shape).
    for lbl in web_labels + be_labels:
        assert "host.docker.internal:host-gateway" not in lbl, f"extra_hosts leaked into a Traefik rule: {lbl}"


def test_prod_redeploy_still_preserves_extra_hosts() -> None:
    """The rename must not lose the preservation behaviour — the backend keeps host.docker.internal."""
    result = _build_prod_redeploy()
    be_extra = result["services"]["backend"].get("extra_hosts") or []
    joined = [str(x) for x in be_extra] if isinstance(be_extra, list) else [f"{k}:{v}" for k, v in be_extra.items()]
    assert any("host.docker.internal:host-gateway" in h for h in joined), joined


# ---------------------------------------------------------------------------
# UAT per-project naming (audit fix 2026-07-11) + project-level path unchanged.
# ---------------------------------------------------------------------------


def test_percustomer_uat_traefik_host_is_per_project() -> None:
    """A per-customer UAT (customer_slug+app) → ``uat-<customer>-<app>``, NEVER the old flat ``uat-<customer>-uat``."""
    compose = build_uat_compose(
        slug="andros-uat",
        project="nex-payables",
        project_path=Path("/opt/projects/nex-payables"),
        source={
            "services": {
                "web": {"image": "app-web:latest", "ports": ["3000:80"]},
                "backend": {"image": "app-be:latest", "ports": ["8000:8000"]},
                "db": {"image": "postgres:16", "environment": {"POSTGRES_PASSWORD": "x"}},
            }
        },
        roles={"frontend": "web", "backend": "backend", "db": "db"},
        db_user="app",
        db_name="app",
        environment="uat",
        customer_slug="andros",
        app="payables",
    )
    web_labels = _label_strings(compose["services"]["web"])
    assert any("Host(`uat-andros-payables.isnex.eu`)" in lbl for lbl in web_labels), web_labels
    for lbl in web_labels:
        assert "uat-andros-uat" not in lbl  # the old flat per-env name is gone


def test_instance_naming_percustomer_vs_project_level() -> None:
    # Per-customer: PROD ``<customer>-<app>``, UAT ``uat-<customer>-<app>``.
    assert _instance_naming("prod", "andros-prod", "andros", "payables")[0] == "andros-payables"
    assert _instance_naming("uat", "andros-uat", "andros", "payables")[0] == "uat-andros-payables"
    # Project-level UAT (no customer_slug — the uat-deploy.py / MÁGERSTAV path) → flat ``uat-<slug>`` UNCHANGED.
    name_base, host = _instance_naming("uat", "inbox", None, None)
    assert name_base == "uat-inbox"
    assert host == "uat-inbox.isnex.eu"
