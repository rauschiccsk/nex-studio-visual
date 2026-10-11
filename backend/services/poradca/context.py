"""What Poradca may know about a project and what it must hide (ICCINT-167, design §4.2 and §4.4).

* :func:`uat_installations` — UAT installations of a project via the customers table (finding B9: the UAT ↔
  customer ↔ project link is read from the cockpit, not from the folder name). PROD is never returned here — Poradca
  does not see the customers' live operation.
* :func:`known_secret_values` — values that the secrets filter replaces by exact match (layer 2).
  The values go only into the filter's memory; they are never written or printed anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.customers import Customer
from backend.db.models.projects import Project
from backend.services import uat_provisioner

logger = logging.getLogger(__name__)

#: Backend environment variables whose values are secrets.
_ENV_SECRETS = ("CLAUDE_CODE_OAUTH_TOKEN", "SECRET_KEY", "GITHUB_TOKEN", "GH_TOKEN", "DEDO_API_TOKEN")
#: Variables with a database address — the secret is the password in it.
_ENV_URLS = ("DATABASE_URL", "TEST_DATABASE_URL")

#: A key whose value is a secret (``DB_PASSWORD``, ``JWT_SECRET``, ``API_KEY``, ``ACCESS_TOKEN``…).
_SECRET_KEY_RE = re.compile(r"(?i)(pass(word|wd)?|pwd|secret|token|api[_-]?key|private[_-]?key|credential|dsn)")


@dataclass(frozen=True)
class UatInstallation:
    """One UAT installation of a project at one customer."""

    customer_slug: str
    customer_name: str
    directory: Path


def uat_installations(db: Session, project: Project) -> list[UatInstallation]:
    """UAT installations of a project that really exist on disk — per customer of the project."""
    from backend.services.deploy import _customer_dir_slug

    out: list[UatInstallation] = []
    customers = db.execute(select(Customer).where(Customer.project_id == project.id)).scalars().all()
    for customer in customers:
        customer_slug = _customer_dir_slug(customer)
        directory = uat_provisioner.instance_dir_for(
            environment="uat", customer_slug=customer_slug, full_project_slug=project.slug
        )
        if (directory / "docker-compose.yml").is_file():
            out.append(UatInstallation(customer_slug, customer.name, directory))
    return out


def _url_password(url: str) -> str:
    try:
        return urlsplit(url).password or ""
    except ValueError:
        return ""


def _secret_values_from_env_text(text: str) -> list[str]:
    values = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        if _SECRET_KEY_RE.search(key):
            values.append(value)
        if "://" in value:
            values.append(_url_password(value))
    return values


def _secret_values_from_json(obj: object, key: str = "") -> list[str]:
    if isinstance(obj, dict):
        return [v for k, child in obj.items() for v in _secret_values_from_json(child, str(k))]
    if isinstance(obj, list):
        return [v for child in obj for v in _secret_values_from_json(child, key)]
    if isinstance(obj, str):
        found = [obj] if _SECRET_KEY_RE.search(key) else []
        if "://" in obj:
            found.append(_url_password(obj))
        return found
    return []


def _values_from_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _values_from_text(text)


def _values_from_text(text: str) -> list[str]:
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return _secret_values_from_json(json.loads(stripped))
        except json.JSONDecodeError:
            pass
    return _secret_values_from_env_text(text)


def known_secret_values(db: Session, project: Project) -> list[str]:
    """Values for exact matching: cockpit environment, settings of the project's UAT installations, the vault.

    The access vault is traversed entirely — hiding more is safer than less — and is read through the vault
    service (:mod:`backend.services.credentials`, the same path guards as the API), not directly from files.
    A record that cannot be read is skipped (the shape filter remains); none of this may stop the question.
    """
    values: list[str] = []
    for name in _ENV_SECRETS:
        values.append(os.environ.get(name, ""))
    for name in _ENV_URLS:
        values.append(_url_password(os.environ.get(name, "")))
    for installation in uat_installations(db, project):
        values += _values_from_file(installation.directory / ".env")
    from backend.services import credentials as credentials_service

    for credential in credentials_service.list_credentials(db):
        try:
            values += _values_from_text(credentials_service.read_content(db, credential.id).content)
        except (OSError, ValueError, LookupError):
            continue
        except Exception:  # noqa: BLE001 — the vault must not stop the question; the shape filter remains
            logger.warning("poradca: credential %s could not be read for redaction", credential.id)
    return [v for v in values if v]
