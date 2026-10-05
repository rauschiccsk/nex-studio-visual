"""Čo Poradca o projekte smie vedieť a čo musí skryť (ICCINT-167, návrh §4.2 a §4.4).

* :func:`uat_installations` — inštalácie UAT projektu cez tabuľku zákazníkov (nález B9: väzba UAT ↔
  zákazník ↔ projekt sa číta z kokpitu, nie z mena priečinka). PROD sa tu nevracia nikdy — Poradca ostrú
  prevádzku zákazníkov nevidí.
* :func:`known_secret_values` — hodnoty, ktoré filter tajomstiev nahradí presnou zhodou (vrstva 2).
  Hodnoty idú len do pamäte filtra; nikde sa nezapisujú ani nevypisujú.
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

from backend.config.settings import settings
from backend.db.models.customers import Customer
from backend.db.models.projects import Project
from backend.services import uat_provisioner

logger = logging.getLogger(__name__)

#: Premenné prostredia backendu, ktorých hodnoty sú tajomstvá.
_ENV_SECRETS = ("CLAUDE_CODE_OAUTH_TOKEN", "SECRET_KEY", "GITHUB_TOKEN", "GH_TOKEN", "DEDO_API_TOKEN")
#: Premenné s adresou databázy — tajomstvom je heslo v nej.
_ENV_URLS = ("DATABASE_URL", "TEST_DATABASE_URL")

#: Kľúč, ktorého hodnota je tajomstvo (``DB_PASSWORD``, ``JWT_SECRET``, ``API_KEY``, ``ACCESS_TOKEN``…).
_SECRET_KEY_RE = re.compile(r"(?i)(pass(word|wd)?|pwd|secret|token|api[_-]?key|private[_-]?key|credential|dsn)")


@dataclass(frozen=True)
class UatInstallation:
    """Jedna inštalácia UAT projektu u jedného zákazníka."""

    customer_slug: str
    customer_name: str
    directory: Path


def uat_installations(db: Session, project: Project) -> list[UatInstallation]:
    """Inštalácie UAT projektu, ktoré na disku naozaj sú — po zákazníkoch projektu."""
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
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return _secret_values_from_json(json.loads(stripped))
        except json.JSONDecodeError:
            pass
    return _secret_values_from_env_text(text)


def known_secret_values(db: Session, project: Project) -> list[str]:
    """Hodnoty pre presnú zhodu filtra: prostredie kokpitu, nastavenia inštalácií UAT projektu, trezor.

    Trezor prístupov sa prechádza celý — viac skrytého je bezpečnejšie než menej. Súbor, ktorý sa nedá
    prečítať, sa preskočí (filter tvarov ostáva); nič z toho nesmie zastaviť otázku.
    """
    values: list[str] = []
    for name in _ENV_SECRETS:
        values.append(os.environ.get(name, ""))
    for name in _ENV_URLS:
        values.append(_url_password(os.environ.get(name, "")))
    for installation in uat_installations(db, project):
        values += _values_from_file(installation.directory / ".env")
    store = Path(settings.credentials_storage_path)
    try:
        files = [p for p in store.rglob("*") if p.is_file()] if store.is_dir() else []
    except OSError:
        files = []
    for path in files:
        values += _values_from_file(path)
    return [v for v in values if v]
