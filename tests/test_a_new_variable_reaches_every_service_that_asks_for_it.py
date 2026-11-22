"""DEV-488 — a variable a new version asks for reaches every service of the installation the project gives it to.

22.11.2026: Orthodox Hermenia 1.1.0 gave the yearly Claude code to its backend and its worker. The deploy said
„doplnené: backend: CLAUDE_CODE_MAX_OUTPUT_TOKENS, CLAUDE_CODE_OAUTH_TOKEN“ — the services were paired by role only
(backend, frontend, database), so the worker, the one that calls Claude, went on without it, and „Overiť spojenie“
never answered. And as the code was now in the .env, the next deploy would not have given it either: a key in the .env
counted as delivered to every service, though Hermenia's services read no .env. Director: „súhlasím“.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from backend.services import uat_provisioner as P

#: The project as Orthodox Hermenia 1.1.0 declares it (the worker and the backend call Claude).
PROJECT = {
    "services": {
        "db": {"image": "pgvector/pgvector:pg16", "environment": {"POSTGRES_DB": "hermenia_dev"}},
        "backend": {
            "image": "orthodox-hermenia-backend:v1.1.0",
            "environment": {"CLAUDE_CODE_OAUTH_TOKEN": "${CLAUDE_CODE_OAUTH_TOKEN}", "APP_ENV": "${APP_ENV}"},
        },
        "worker": {
            "image": "orthodox-hermenia-worker:v1.1.0",
            "environment": {"CLAUDE_CODE_OAUTH_TOKEN": "${CLAUDE_CODE_OAUTH_TOKEN}", "APP_ENV": "${APP_ENV}"},
        },
        "test": {"image": "orthodox-hermenia-backend:v1.1.0", "environment": {"TEST_ONLY": "1"}},
    }
}

#: The installation after the 1.1.0 deploy: the backend got the code, the worker did not; the code is in the .env.
INSTALLATION = {
    "name": "uat-skit-orthodox-hermenia",
    "services": {
        "db": {"image": "pgvector/pgvector:pg16", "environment": {"POSTGRES_DB": "hermenia"}},
        "backend": {
            "image": "orthodox-hermenia-backend:v1.0.0",
            "environment": {"CLAUDE_CODE_OAUTH_TOKEN": "${CLAUDE_CODE_OAUTH_TOKEN}", "APP_ENV": "${APP_ENV}"},
        },
        "worker": {"image": "orthodox-hermenia-worker:v1.0.0", "environment": {"APP_ENV": "${APP_ENV}"}},
    },
}


def _installation(tmp_path: Path, compose: dict = INSTALLATION) -> Path:
    d = tmp_path / "skit" / "orthodox-hermenia"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
    (d / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=kod\nAPP_ENV=uat\n", encoding="utf-8")
    return d


def test_the_worker_is_paired_by_its_name_and_gets_what_it_misses(tmp_path):
    d = _installation(tmp_path)

    assert P.env_keys_missing_against_source(d, PROJECT) == {"worker": ["CLAUDE_CODE_OAUTH_TOKEN"]}
    novy = P.render_version_bump(
        d, version="1.1.0", project_slug="orthodox-hermenia", source=PROJECT, project_path=tmp_path
    )
    assert novy["services"]["worker"]["environment"]["CLAUDE_CODE_OAUTH_TOKEN"] == "${CLAUDE_CODE_OAUTH_TOKEN}"
    assert novy["services"]["worker"]["environment"]["APP_ENV"] == "${APP_ENV}", "what it has stays"


def test_a_key_in_the_env_is_delivered_only_to_a_service_that_reads_the_env(tmp_path):
    reading = {**INSTALLATION, "services": {**INSTALLATION["services"]}}
    reading["services"]["worker"] = {**INSTALLATION["services"]["worker"], "env_file": [".env"]}
    d = _installation(tmp_path, reading)

    assert P.env_keys_missing_against_source(d, PROJECT) == {}, "the worker reads the .env — it has the code"


def test_services_pair_by_role_first_then_by_name_and_never_twice():
    project = {"services": {"db": {"image": "postgres:16"}, "worker": {}, "migrate": {}, "test": {}}}
    installation = {"services": {"postgres": {"image": "postgres:16"}, "worker": {}, "migrate": {}}}

    assert P._service_pairs(project["services"], installation["services"]) == [
        ("db", "postgres"),
        ("worker", "worker"),
        ("migrate", "migrate"),
    ]
