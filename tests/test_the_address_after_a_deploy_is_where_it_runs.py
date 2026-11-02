"""DEV-283 — the address a deploy to another machine answers with is the installation's name on that machine.

02.11.2026 the Director deployed ICC's NEX Manager to MAGER: „Tvoj URL, ktorý si tu vypísal funguje, ale to čo je
v kokpite nie. Chyba je zlá doména: https://icc-manager.isnex.eu/“. DEV-275 had taught the address of a PROD
installation the zone of its machine with a parameter that defaulted to this machine; the link on the PROD table got
it, the deploy runner and the adoption runner did not — the default hid that they were left behind.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from backend.services import deploy as deploy_service
from backend.services import instance_adoption, orchestrator, uat_provisioner


class _Provisioned:
    def __init__(self, tmp_path: Path):
        self.fe_service = "frontend"
        self.warnings: list[str] = []
        self.compose_path = tmp_path / "docker-compose.yml"
        self.uat_dir = tmp_path
        self.previous_remote_files = {}


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    """A deploy that went through — only the address the runner composes is real."""
    monkeypatch.setattr(uat_provisioner, "provision_uat", lambda *a, **k: _Provisioned(tmp_path))
    monkeypatch.setattr(uat_provisioner, "derive_uat_slug", lambda slug: "manager")
    monkeypatch.setattr(uat_provisioner, "assert_no_installation_here", lambda *a, **k: None)
    monkeypatch.setattr(instance_adoption, "set_aside_hand_authored", lambda *a, **k: [])

    async def _health(*_a, **_k):
        return None

    async def _prod(*_a, **_k):
        return orchestrator.InstanceDeployResult(True, "OK", new_version_started=True)

    monkeypatch.setattr(orchestrator, "instance_health", _health)
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _prod)


RUN = {"project_slug": "nex-manager", "uat_slug": "icc-prod", "version_number": "1.4.2", "force_fresh": False}


async def test_a_deploy_to_mager_answers_with_the_name_on_mager(deployed):
    there = await deploy_service._default_deploy_runner(**RUN, deploy_host="mager")
    here = await deploy_service._default_deploy_runner(**RUN)

    assert there[2] == "https://icc-manager.icc.sk"
    assert here[2] == "https://icc-manager.isnex.eu"


async def test_an_adoption_on_mager_answers_with_the_name_on_mager(deployed):
    there = await instance_adoption.adopting_deploy_runner(**RUN, deploy_host="mager")
    here = await instance_adoption.adopting_deploy_runner(**RUN)

    assert there[2] == "https://icc-manager.icc.sk"
    assert here[2] == "https://icc-manager.isnex.eu"


def test_the_machine_cannot_be_left_out_of_a_prod_address():
    parameter = inspect.signature(deploy_service._prod_url).parameters["prod_host"]

    assert parameter.default is inspect.Parameter.empty, "a default lets a caller forget the machine unnoticed"
    with pytest.raises(TypeError):
        deploy_service._prod_url("icc", "manager", private=False)  # type: ignore[call-arg]
