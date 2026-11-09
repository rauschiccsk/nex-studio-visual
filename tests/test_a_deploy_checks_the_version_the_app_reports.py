"""DEV-336 — after a deploy the cockpit compares the version the app reports with the version it deployed.

09.11.2026 Máger Dedo found Author Asistent 1.0.0 in TATA's PROD reporting „version": "0.1.0" — its health took the
number from a package nobody had raised. The deploy had ended as a success: the cockpit checked that the app answers
and can be entered, not which version it says it is. An installation running another version (an old image, a number
written in by hand) passed the same way. Director to the fix: „Súhlasím“. Now a mismatch is a warning beside the
customer's row with both numbers; an app whose health names no version says so; one whose health cannot be read is
no claim at all (the other checks speak for it).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.services import deploy as deploy_service
from backend.services import orchestrator as O
from backend.services import uat_provisioner
from backend.services.orchestrator import instance_version as real_instance_version  # before the suite's stub

# ── what the comparison says ───────────────────────────────────────────────────────────────────────────────────────


def test_the_version_the_app_reports_is_the_deployed_one_nothing_is_said():
    assert O.version_mismatch("1.0.1", "1.0.1") is None
    assert O.version_mismatch("1.0.1", "v1.0.1") is None, "a leading v is the same version"
    assert O.version_mismatch("v1.10.3", " 1.10.3 ") is None


def test_another_version_is_said_with_both_numbers():
    assert O.version_mismatch("1.0.0", "0.1.0") == O.VERSION_MISMATCH.format(reported="0.1.0", deployed="1.0.0")
    assert "0.1.0" in O.VERSION_MISMATCH.format(reported="0.1.0", deployed="1.0.0")
    assert "1.0.0" in O.VERSION_MISMATCH.format(reported="0.1.0", deployed="1.0.0")


def test_an_app_that_names_no_version_says_so():
    assert O.version_mismatch("1.0.0", "") == O.VERSION_NOT_REPORTED.format(deployed="1.0.0")


def test_a_health_that_cannot_be_read_is_no_claim():
    assert O.version_mismatch("1.0.0", None) is None


# ── how it is read: the app's own health, inside its backend, where the installation runs ─────────────────────────


@pytest.fixture()
def installation(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {"services": {"db": {"image": "postgres:16"}, "backend": {"build": ".", "ports": ["8000:8000"]}}}
        ),
        encoding="utf-8",
    )
    return compose


@pytest.mark.parametrize(
    ("printed", "rc", "expected"),
    [
        ('"0.1.0"', 0, "0.1.0"),
        ('""', 0, ""),  # the health answered without a version
        ("null", 0, None),  # no health to read
        ("", 1, None),  # the backend is not running
        ("not json", 0, None),
    ],
)
async def test_the_version_is_read_from_the_health_of_the_backend(installation, monkeypatch, printed, rc, expected):
    asked: list[list[str]] = []

    async def step(cmd, timeout, env=None):
        asked.append(cmd)
        return rc, printed

    monkeypatch.setattr(O, "_compose_smoke_step", step)

    assert await real_instance_version(installation, "author-asistent") == expected
    assert asked[0][:7] == ["docker", "compose", "-f", str(installation), "exec", "-T", "backend"]


class _Provisioned:
    warnings: list[str] = []
    fe_service = "frontend"
    compose_path = Path("/nonexistent/docker-compose.yml")


@pytest.fixture
def deployed(monkeypatch):
    """The provisioner writes, the stack comes up „OK“, nothing got worse — nothing touches Docker."""
    monkeypatch.setattr(uat_provisioner, "provision_uat", lambda *a, **k: _Provisioned())

    async def up(*_a, **_k):
        return True, "OK"

    async def nothing_worse(*_a, **_k):
        return []

    monkeypatch.setattr(O, "_run_uat_deploy", up)
    monkeypatch.setattr(O, "health_regressions", nothing_worse)


# ── the deploy says it ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_deploy_warns_when_the_app_reports_another_version(deployed, monkeypatch):
    async def reported(compose: Path, project_slug: str, deploy_host=None):
        return "0.1.0"

    monkeypatch.setattr(O, "instance_version", reported)

    result = await deploy_service._default_deploy_runner(
        project_slug="author-asistent", uat_slug="tata-uat", version_number="1.0.0", force_fresh=False
    )

    assert O.VERSION_MISMATCH.format(reported="0.1.0", deployed="1.0.0") in result.warnings


async def test_the_deploy_says_nothing_when_the_app_reports_the_deployed_version(deployed, monkeypatch):
    async def reported(compose: Path, project_slug: str, deploy_host=None):
        return "1.0.0"

    monkeypatch.setattr(O, "instance_version", reported)

    result = await deploy_service._default_deploy_runner(
        project_slug="author-asistent", uat_slug="tata-uat", version_number="1.0.0", force_fresh=False
    )

    assert not [w for w in result.warnings if "verziu" in w and "hlási" in w]


def test_uat_provisioner_names_the_backend_the_probe_asks(installation):
    """The probe asks the service the cockpit calls backend — the same role every other post-deploy check uses."""
    services = yaml.safe_load(installation.read_text(encoding="utf-8"))["services"]
    assert uat_provisioner.identify_service_roles(services)["backend"] == "backend"
