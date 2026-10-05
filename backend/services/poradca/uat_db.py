"""Databáza UAT pre Poradcu — len na čítanie, stráži to DATABÁZA (ICCINT-167, návrh §4.3, nálezy B4, B5).

V databáze inštalácie UAT je účet :data:`ROLE` bez práv správcu, s ``default_transaction_read_only = on``
a ``statement_timeout = 10s``, a s právom SELECT **len na stĺpce bez tajných mien** v obyčajných tabuľkách
schémy ``public``. Stĺpec s heslom tak nevydá ani ``row_to_json``, alias, ``substr`` či pohľad — odmietne
ho databáza, nie filter. Tvarový filter výstupu (:mod:`.secrets_filter`) ostáva ako druhá vrstva.

Účet zakladá :func:`ensure_role` — krok po spustení UAT pri každom nasadení (zachytí aj zmeny schémy:
práva sa najprv odoberú a potom znova udelia podľa aktuálnych stĺpcov). Do ručne písaného predpisu
inštalácie sa nesiaha; databáza s rozšírením ``dblink`` alebo ``postgres_fdw`` účet nedostane vôbec.

Dotaz beží cez ``docker exec -u postgres <db> psql -U poradca_ro`` ako POLE argumentov (žiadny shell);
dotaz so spätnou lomkou sa odmietne (príkazy ``psql`` ako ``\\!``), a viac príkazov v jednom tiež.
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

#: Meno roly v PostgreSQL, ktoré smie ísť do ``psql -U`` (z ``POSTGRES_USER`` kontajnera).
_PG_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

#: Stĺpec s takýmto menom Poradca nedostane. Široko — radšej nevidieť stĺpec navyše než heslo. ``cipher`` /
#: ``nonce``: zašifrované tajomstvo (NEX Inbox ``email_credentials.ciphertext``) je tiež materiál k prístupom.
SECRET_COLUMN_PATTERN = (
    r"(pass|pwd|secret|token|hash|salt|key|session|otp|credential|private|cookie|signature|cipher|nonce)"
)
#: Tabuľku s takýmto menom Poradca nedostane vôbec — ani jej „nevinné" stĺpce (zmerané 05.10.2026 v živom
#: UAT NEX Inboxu: ``email_credentials`` mala skrytý len ``key``, šifrovaný text bol čitateľný).
SECRET_TABLE_PATTERN = r"(credential|secret|password|token|session)"

_FORBIDDEN_EXTENSIONS = ("dblink", "postgres_fdw")
_ALLOWED_START = re.compile(r"^\s*(select|with|explain)\b", re.IGNORECASE)


def _strip_comments(query: str) -> str:
    out = re.sub(r"/\*.*?\*/", " ", query, flags=re.S)
    return re.sub(r"--[^\n]*", " ", out)


def query_problem(query: str) -> Optional[str]:
    """Veta pre agenta, prečo dotaz nepustíme; ``None``, keď je v poriadku. Skutočnú stráž drží databáza."""
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
    """SELECT/WITH sa obalí stropom riadkov; EXPLAIN sa pustí, ako je."""
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
    """``(kontajner databázy, meno databázy)`` inštalácie — z jej predpisu a štítkov kontajnera."""
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
    if len(lines) > rows + 1:  # hlavička + riadky
        lines = lines[: rows + 1] + [f"… (zobrazených prvých {rows} riadkov)"]
    return f"Dotaz:\n{sql}\n\nVýsledok (UAT {installation.customer_slug}):\n" + "\n".join(lines)


def role_setup_sql(db_name: str) -> str:
    """Skript, ktorý účet Poradcu založí alebo zosúladí so súčasnou schémou. Opakovateľný."""
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
    """Založí alebo zosúladí účet Poradcu v databáze inštalácie UAT. Nikdy nevyhodí výnimku.

    Vráti ``(ok, veta)`` — nasadenie UAT kvôli Poradcovi nezlyhá; veta ide do jeho záznamu.
    """
    try:
        return await _ensure_role(UatInstallation(customer_slug, customer_slug, installation_dir))
    except Exception:  # noqa: BLE001 — nasadenie UAT kvôli Poradcovi nikdy nezlyhá
        logger.exception("poradca: role setup crashed in %s", installation_dir)
        return False, f"Účet Poradcu v databáze UAT {customer_slug} sa nepodarilo pripraviť."


async def _ensure_role(installation: UatInstallation) -> tuple[bool, str]:
    customer_slug = installation.customer_slug
    try:
        container, db_name = await _db_container(installation)
    except ToolError as exc:
        return False, str(exc)
    # Správca databázy je ten z nastavenia inštalácie (nexmanager, nexweb, …) — rola ``postgres`` v živých
    # UAT neexistuje (zmerané 05.10.2026 pred prvým zápisom). Miestny socket ho pustí bez hesla.
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
