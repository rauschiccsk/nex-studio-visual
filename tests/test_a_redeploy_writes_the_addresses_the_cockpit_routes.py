"""DEV-164 — a redeploy writes the addresses the cockpit decides as the installation's real ones, not the template's.

22.10.2026 Career Asistent 1.1.0 got its web on the UAT of ICC and its administration refused every sign-in
(``foreign_origin``). ``ADMIN_PUBLIC_URL`` (``@public-url-admin``) was new to the installation, a redeploy only fills
what is missing from ``.env.example`` (:func:`uat_provisioner.env_version_bump`), so the administration was told its
address is ``https://career.example.internal:10251`` and took its own for a foreign one. Director 22.10.2026:
„súhlasím“. An address marked ``@public-url``/``@public-url-admin`` is the host the cockpit routes — written on every
deploy where the cockpit wrote the routing; an installation with its own routing keeps its addresses and is told.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_an_installation_made_before_the_web_switch_gets_its_web import (  # noqa: F401 — the fixture by name
    ADMIN_HOST,
    HOST,
    _deploy,
    _switch_to_web,
    career,
)

#: Career Asistent's address markers with the examples its ``.env.example`` carries (22.10.2026).
MAIN = "# @public-url\nAPP_PUBLIC_URL=https://career.example.internal:10255\nSESSION_SECRET=\n"
ADMIN = "# @public-url-admin\nADMIN_PUBLIC_URL=https://career.example.internal:10251\n"


def _env_path(uat: Path) -> Path:
    return uat / "icc" / "career-asistent" / ".env"


def _addresses(uat: Path) -> dict[str, str]:
    lines = _env_path(uat).read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line.split("=", 1)[0] in ("APP_PUBLIC_URL", "ADMIN_PUBLIC_URL"))


def _set_in_env(uat: Path, key: str, value: str) -> None:
    lines = _env_path(uat).read_text(encoding="utf-8").splitlines()
    _env_path(uat).write_text(
        "\n".join(f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines) + "\n", encoding="utf-8"
    )


def test_the_administration_gets_its_own_address_not_the_templates_example(career):  # noqa: F811
    project, projects, uat, surfaces = career
    (project / ".env.example").write_text(MAIN, encoding="utf-8")
    _deploy(projects, uat, "1.0.0")
    _switch_to_web(project, surfaces)
    (project / ".env.example").write_text(MAIN + ADMIN, encoding="utf-8")

    _deploy(projects, uat, "1.1.0")

    assert _addresses(uat) == {"APP_PUBLIC_URL": f"https://{HOST}", "ADMIN_PUBLIC_URL": f"https://{ADMIN_HOST}"}


def test_an_address_that_is_not_the_installations_is_written_again(career):  # noqa: F811
    """A project with one frontend too — the address is the cockpit's on every deploy, not only when it is new."""
    project, projects, uat, _surfaces = career
    (project / ".env.example").write_text(MAIN, encoding="utf-8")
    _deploy(projects, uat, "1.0.0")
    _set_in_env(uat, "APP_PUBLIC_URL", "https://career.example.internal:10255")

    result = _deploy(projects, uat, "1.0.1")

    assert _addresses(uat) == {"APP_PUBLIC_URL": f"https://{HOST}"}
    assert not [w for w in result.warnings if "vlastné smerovanie" in w]


def test_an_installation_with_its_own_routing_keeps_its_addresses_and_is_told(career):  # noqa: F811
    project, projects, uat, surfaces = career
    (project / ".env.example").write_text(MAIN, encoding="utf-8")
    _deploy(projects, uat, "1.0.0")
    compose_path = uat / "icc" / "career-asistent" / "docker-compose.yml"
    compose_path.write_text(compose_path.read_text(encoding="utf-8").replace(HOST, "kariera.icc.sk"), encoding="utf-8")
    _set_in_env(uat, "APP_PUBLIC_URL", "https://kariera.icc.sk")
    _switch_to_web(project, surfaces)
    (project / ".env.example").write_text(MAIN + ADMIN, encoding="utf-8")

    result = _deploy(projects, uat, "1.1.0")

    assert _addresses(uat) == {
        "APP_PUBLIC_URL": "https://kariera.icc.sk",
        "ADMIN_PUBLIC_URL": "https://career.example.internal:10251",
    }
    assert (
        "Inštalácia má vlastné smerovanie, preto jej kokpit adresy nezapisuje; tieto premenné majú hodnotu z príkladu "
        "projektu: ADMIN_PUBLIC_URL — nastav ich v inštalácii ručne."
    ) in result.warnings
