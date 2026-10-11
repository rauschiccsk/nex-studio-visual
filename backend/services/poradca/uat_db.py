"""UAT database for Poradca — read-only, guarded by the DATABASE (ICCINT-167, design §4.3, findings B4, B5).

In the database of a UAT installation there is an account :data:`ROLE` without admin rights, with
``default_transaction_read_only = on``, ``statement_timeout = 10s`` and SELECT **only on columns without secret names**
in ordinary tables of the ``public`` schema. A password column is thus not yielded by ``row_to_json``, alias, ``substr``
or a view — the database refuses it, not a filter. The output shape filter (:mod:`.secrets_filter`) is a second layer.

The account is created by :func:`ensure_role` — a step after UAT starts on every deployment (catches schema changes too:
rights are first revoked and then granted again according to the current columns). A hand-written installation
descriptor is not touched; a database with the ``dblink`` or ``postgres_fdw`` extension gets no account at all.

The query runs via ``docker exec -u postgres <db> psql -U poradca_ro`` as an ARRAY of arguments (no shell);
a query with a backslash is rejected (``psql`` commands such as ``\\!``), and so is more than one statement in one.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from backend.services import uat_provisioner
from backend.services.poradca.context import UatInstallation
from backend.services.poradca.mcp_server import ToolError
from backend.services.poradca.secrets_filter import SecretFilter

logger = logging.getLogger(__name__)

ROLE = "poradca_ro"

#: PostgreSQL role name that may go into ``psql -U`` (from the container's ``POSTGRES_USER``).
_PG_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

#: Poradca does not get a column with such a name. Broad — better to miss an extra column than a password. ``cipher`` /
#: ``nonce``: an encrypted secret (NEX Inbox ``email_credentials.ciphertext``) is also material for access.
SECRET_COLUMN_PATTERN = (
    r"(pass|pwd|secret|token|hash|salt|key|session|otp|credential|private|cookie|signature|cipher|nonce)"
)
#: Poradca gets no table with such a name at all — not even its "innocent" columns (measured 05.10.2026 in the live
#: UAT of NEX Inbox: ``email_credentials`` had only ``key`` hidden, the encrypted text was readable).
SECRET_TABLE_PATTERN = r"(credential|secret|password|token|session)"

_FORBIDDEN_EXTENSIONS = ("dblink", "postgres_fdw")
_ALLOWED_START = re.compile(r"^\s*(select|with|explain)\b", re.IGNORECASE)


def _strip_comments(query: str) -> str:
    out = re.sub(r"/\*.*?\*/", " ", query, flags=re.S)
    return re.sub(r"--[^\n]*", " ", out)


def query_problem(query: str) -> Optional[str]:
    """Sentence for the agent on why the query is refused; ``None`` when fine. The real guard is the database."""
    if not query.strip():
        return "Dotaz je prázdny."
    if "\\" in query:
        return "Dotaz nesmie obsahovať spätnú lomku."
    body = _strip_comments(query).strip().rstrip(";").strip()
    if ";" in body:
        return "Povolený je jeden príkaz."
    if not _ALLOWED_START.match(body):
        return "Povolené sú len dotazy SELECT, WITH a EXPLAIN."
    return None


def _wrapped(query: str, limit: int) -> str:
    """SELECT/WITH is wrapped in a row cap; EXPLAIN is run as is."""
    body = _strip_comments(query).strip().rstrip(";").strip()
    if re.match(r"^\s*explain\b", body, re.IGNORECASE):
        return body
    return f"SELECT * FROM (\n{body}\n) AS poradca_dotaz LIMIT {int(limit)}"


async def _docker(*argv: str, stdin: Optional[bytes] = None, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, f"docker CLI nie je k dispozícii ({exc})"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timeout"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _db_service(installation: UatInstallation) -> Optional[str]:
    try:
        compose = yaml.safe_load((installation.directory / "docker-compose.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    services = compose.get("services") or {}
    return uat_provisioner.identify_service_roles(services).get("db")


async def _db_container(installation: UatInstallation) -> tuple[str, str]:
    """``(database container, database name)`` of the installation — from its descriptor and the container labels."""
    service = _db_service(installation)
    if not service:
        raise ToolError(f"Inštalácia UAT {installation.customer_slug} nemá databázu, ktorú by kokpit rozpoznal.")
    code, out = await _docker(
        "ps",
        "--filter",
        f"label=com.docker.compose.project.working_dir={installation.directory}",
        "--filter",
        f"label=com.docker.compose.service={service}",
        "--format",
        "{{.Names}}",
    )
    names = [n for n in out.split() if n] if code == 0 else []
    if not names:
        raise ToolError(f"Databáza UAT {installation.customer_slug} nebeží.")
    container = names[0]
    code, db_name = await _docker("exec", container, "printenv", "POSTGRES_DB")
    db_name = db_name.strip() if code == 0 else ""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", db_name):
        raise ToolError(f"Meno databázy UAT {installation.customer_slug} sa nedá zistiť.")
    return container, db_name


async def run_query(installation: UatInstallation, query: str, *, rows: int, timeout_s: int) -> str:
    container, db_name = await _db_container(installation)
    sql = _wrapped(query, rows + 1)
    code, out = await _docker(
        "exec",
        "-u",
        "postgres",
        "-e",
        f"PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout={timeout_s * 1000}",
        container,
        "psql",
        "-X",
        "-A",
        "-F",
        " | ",
        "--pset=footer=off",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        ROLE,
        "-d",
        db_name,
        "-c",
        sql,
        timeout=timeout_s + 15,
    )
    if code != 0:
        if f'role "{ROLE}" does not exist' in out:
            raise ToolError(
                f"V databáze UAT {installation.customer_slug} ešte nie je účet len na čítanie pre Poradcu — "
                "vznikne pri najbližšom nasadení UAT."
            )
        return f"Databáza dotaz odmietla:\n{out.strip()[:2000]}"
    lines = out.rstrip("\n").splitlines()
    if len(lines) > rows + 1:  # header + rows
        lines = lines[: rows + 1] + [f"… (zobrazených prvých {rows} riadkov)"]
    return f"Dotaz:\n{sql}\n\nVýsledok (UAT {installation.customer_slug}):\n" + "\n".join(lines)


def role_setup_sql(db_name: str) -> str:
    """Script that creates the Poradca account or reconciles it with the current schema. Repeatable."""
    return f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname IN ('{"', '".join(_FORBIDDEN_EXTENSIONS)}')) THEN
    RAISE EXCEPTION 'poradca: databaza ma rozsirenie na pristup k inym databazam — ucet nevznikne';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN;
  END IF;
END $$;
ALTER ROLE {ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS LOGIN;
ALTER ROLE {ROLE} SET default_transaction_read_only = on;
ALTER ROLE {ROLE} SET statement_timeout = '10s';
REVOKE ALL ON DATABASE "{db_name}" FROM {ROLE};
GRANT CONNECT ON DATABASE "{db_name}" TO {ROLE};
REVOKE ALL ON SCHEMA public FROM {ROLE};
GRANT USAGE ON SCHEMA public TO {ROLE};
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT table_schema, table_name, column_name FROM information_schema.column_privileges
           WHERE grantee = '{ROLE}' LOOP
    EXECUTE format('REVOKE ALL (%I) ON %I.%I FROM {ROLE}', r.column_name, r.table_schema, r.table_name);
  END LOOP;
  FOR r IN SELECT c.table_schema, c.table_name, c.column_name
           FROM information_schema.columns c
           JOIN information_schema.tables t
             ON t.table_schema = c.table_schema AND t.table_name = c.table_name
           WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
             AND c.table_name !~* '{SECRET_TABLE_PATTERN}'
             AND c.column_name !~* '{SECRET_COLUMN_PATTERN}' LOOP
    EXECUTE format('GRANT SELECT (%I) ON %I.%I TO {ROLE}', r.column_name, r.table_schema, r.table_name);
  END LOOP;
END $$;
"""


async def ensure_role(installation_dir: Path, customer_slug: str) -> tuple[bool, str]:
    """Creates or reconciles the Poradca account in the database of a UAT installation. Never raises an exception.

    Returns ``(ok, sentence)`` — UAT deployment does not fail because of Poradca; the sentence goes to its record.
    """
    try:
        return await _ensure_role(UatInstallation(customer_slug, customer_slug, installation_dir))
    except Exception:  # noqa: BLE001 — a UAT deployment never fails because of Poradca
        logger.exception("poradca: role setup crashed in %s", installation_dir)
        return False, f"Účet Poradcu v databáze UAT {customer_slug} sa nepodarilo pripraviť."


async def _ensure_role(installation: UatInstallation) -> tuple[bool, str]:
    customer_slug = installation.customer_slug
    try:
        container, db_name = await _db_container(installation)
    except ToolError as exc:
        return False, str(exc)
    # The DB admin is the one from the installation settings (nexmanager, nexweb, …) — the ``postgres`` role does not
    # exist in the live UAT (measured 05.10.2026 before the first write). The local socket admits it without a password.
    code, superuser = await _docker("exec", container, "printenv", "POSTGRES_USER")
    superuser = (superuser.strip() if code == 0 else "") or "postgres"
    if not _PG_ROLE_RE.match(superuser):
        return False, f"Účet Poradcu v databáze UAT {customer_slug}: meno správcu databázy sa nedá bezpečne použiť."
    code, out = await _docker(
        "exec",
        "-i",
        "-u",
        "postgres",
        container,
        "psql",
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        superuser,
        "-d",
        db_name,
        stdin=role_setup_sql(db_name).encode("utf-8"),
    )
    if code != 0:
        logger.warning(
            "poradca: role setup failed in %s: %s", installation.directory, SecretFilter()(out.strip())[:500]
        )
        return False, f"Účet Poradcu v databáze UAT {customer_slug} sa nepodarilo pripraviť."
    return True, f"Účet Poradcu v databáze UAT {customer_slug} je pripravený (len čítanie, bez tajných stĺpcov)."
