"""Database backup of an installation before a PROD deploy replaces its containers (DEV-116).

**Why.** 14.10.2026 NEX Inbox 1.8.0 went to MÁGERSTAV's PROD with migration 026 and nothing to go back to: the
cockpit had never backed up a database before a deploy — ``pg_dump`` appeared nowhere in its backend. The backup
before 1.7.1 (11.10.2026) was Máger Dedo's own step in SERVER-105, not the cockpit's. Director: „Súhlasím“ — every
PROD deploy backs the database up first, and when it cannot, the deploy does not start.

**When.** After the images are built (the running version still serves) and before ``up --force-recreate``. A
failure here costs nothing but the deploy itself: no container has been replaced and no migration has run.

**How, on a machine the cockpit reaches only through Docker** (:mod:`remote_instance`). ``pg_dump`` runs inside the
installation's own database container, with the user and the database taken from that container's environment, so
no argument carries anything secret. Its output streams into a short-lived container that writes it into the
installation folder on the SAME machine — the backup lies next to the data it protects, where whoever repairs that
machine looks for it. The written file is then read back by ``pg_restore -l`` of the running database container,
so a dump that cannot be restored is a failed backup, not a file that merely exists.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from backend.config.settings import settings
from backend.services import remote_instance, uat_provisioner

#: Folder of the backups inside the installation folder (owner 1000, mode 700; the files 600).
BACKUP_DIR = "pre-deploy-backups"

#: The database user and database come from the container's own environment — expanded INSIDE the container.
_DB = '-U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}"'
_DUMP_SH = f"exec pg_dump {_DB} -Fc"
_SIZE_SH = f'exec psql {_DB} -Atc "SELECT pg_database_size(current_database())"'
_LIST_SH = "exec pg_restore -l"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

#: The sentence that says where the backup lies. The deploy screen finds it in the report by these words
#: (frontend/src/components/deploy/DeployMatrixPage.tsx, BACKUP_SENTENCE) — a guard test keeps the two equal.
BACKUP_SENTENCE = "Záloha databázy pred nasadením:"

#: What the person reads when the deploy stops here — the running version was not touched.
_NOT_STARTED = "Nasadenie sa nezačalo, bežiaca verzia ostáva."


@dataclass(frozen=True)
class BackupOutcome:
    """``ok`` False = do not replace the containers. ``detail`` is a sentence for the person."""

    ok: bool
    detail: str
    path: Optional[str] = None


def _compose(compose: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(compose), *args]


def _tool(instance_dir: Path, script: str, *, readonly: bool = False, interactive: bool = False) -> list[str]:
    """A short-lived container with the installation folder mounted — the only way to touch files on the target.

    Read-only steps use ``--mount … readonly`` (it never creates a missing folder); the writing ones ``-v``.
    """
    mount = (
        ["--mount", f"type=bind,source={instance_dir},target=/target,readonly"]
        if readonly
        else ["-v", f"{instance_dir}:/target"]
    )
    return [
        "docker",
        "run",
        "--rm",
        *(["-i"] if interactive else []),
        *mount,
        remote_instance.IMAGE,
        "sh",
        "-c",
        script,
    ]


async def _run(argv: list[str], env: Optional[dict[str, str]], timeout: float) -> tuple[int, str]:
    """One command → ``(exit code, output)``; a spawn failure or a timeout is a failed command, never an exception."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1.0, timeout))
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "časový limit"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


async def _pipe(
    source: list[str], sink: list[str], env: Optional[dict[str, str]], timeout: float
) -> tuple[int, int, str]:
    """``source | sink`` without a shell and without the data passing through memory here.

    Returns ``(source exit, sink exit, output of both)``. Over ``DOCKER_HOST=ssh://`` the bytes travel target →
    cockpit → target, but nothing is stored on the cockpit's side.
    """
    read_fd, write_fd = os.pipe()
    try:
        try:
            consumer = await asyncio.create_subprocess_exec(
                *sink, stdin=read_fd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
            )
        except OSError as exc:
            return 127, 127, str(exc)
        try:
            producer = await asyncio.create_subprocess_exec(
                *source, stdin=asyncio.subprocess.DEVNULL, stdout=write_fd, stderr=asyncio.subprocess.PIPE, env=env
            )
        except OSError as exc:
            consumer.kill()
            return 127, 127, str(exc)
    finally:
        # The children hold their own copies; ours must close, or the sink never sees the end of the data.
        os.close(write_fd)
        os.close(read_fd)
    try:
        (_, err), (out, _) = await asyncio.wait_for(
            asyncio.gather(producer.communicate(), consumer.communicate()), timeout=max(1.0, timeout)
        )
    except asyncio.TimeoutError:
        producer.kill()
        consumer.kill()
        return 124, 124, "časový limit"
    text = ((err or b"") + (out or b"")).decode("utf-8", "replace").strip()
    return producer.returncode or 0, consumer.returncode or 0, text


def _db_service(compose: Path) -> Optional[str]:
    data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    return uat_provisioner.identify_service_roles(data.get("services") or {}).get("db")


def _file_name(project_slug: str, version: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{_SAFE_NAME.sub('-', project_slug)}-{_SAFE_NAME.sub('-', version)}-{stamp}.dump"


def _gib(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


def _size(n: int) -> str:
    return f"{n / 1024**2:.1f} MB" if n >= 1024**2 else f"{max(1, n // 1024)} kB"


async def back_up(
    compose: Path, *, project_slug: str, version: str, deploy_host: Optional[str], timeout: float
) -> BackupOutcome:
    """Back up the installation's database before its containers are replaced → :class:`BackupOutcome`.

    Never raises. Nothing to back up (no database service, or an installation with no containers yet — a first
    deploy) is ``ok`` with a sentence saying so; every other problem is a stop.
    """
    deadline = asyncio.get_running_loop().time() + timeout

    def left() -> float:
        return deadline - asyncio.get_running_loop().time()

    instance_dir = compose.parent
    env = remote_instance.docker_env(deploy_host)
    try:
        service = _db_service(compose)
    except (OSError, yaml.YAMLError) as exc:
        return BackupOutcome(
            False, f"Predpis inštalácie sa nedá prečítať, záloha sa nedá urobiť ({exc}). {_NOT_STARTED}"
        )
    if not service:
        return BackupOutcome(True, "Inštalácia nemá databázu — záloha nebola potrebná.")

    code, out = await _run(_compose(compose, "ps", "-a", "-q"), env, left())
    if code != 0:
        return BackupOutcome(False, f"Kontajnery inštalácie sa nedali zistiť: {out[-200:]}. {_NOT_STARTED}")
    if not out:
        return BackupOutcome(True, "Inštalácia ešte nemá kontajnery — databázu nie je čo zálohovať.")
    code, out = await _run(_compose(compose, "ps", "-q", service), env, left())
    if code != 0 or not out:
        return BackupOutcome(
            False, f"Databáza inštalácie (služba {service}) nebeží, zálohu nemožno urobiť. {_NOT_STARTED}"
        )

    # Room first: a dump that fills the customer's disk would stop the very installation it is meant to protect.
    code, out = await _run(_compose(compose, "exec", "-T", service, "sh", "-c", _SIZE_SH), env, left())
    db_bytes = int(out.splitlines()[-1]) if code == 0 and out and out.splitlines()[-1].isdigit() else None
    if db_bytes is None:
        return BackupOutcome(False, f"Veľkosť databázy sa nedala zistiť: {out[-200:]}. {_NOT_STARTED}")
    code, out = await _run(_tool(instance_dir, "df -Pk /target", readonly=True), env, left())
    fields = out.splitlines()[-1].split() if code == 0 and out else []
    free_bytes = int(fields[3]) * 1024 if len(fields) >= 4 and fields[3].isdigit() else None
    if free_bytes is None:
        return BackupOutcome(False, f"Voľné miesto na cieli sa nedalo zistiť: {out[-200:]}. {_NOT_STARTED}")
    reserve = settings.predeploy_backup_min_free_bytes
    if free_bytes < db_bytes + reserve:
        return BackupOutcome(
            False,
            f"Na zálohu nie je miesto: voľných {_gib(free_bytes)}, databáza má {_size(db_bytes)} a rezerva disku je "
            f"{_gib(reserve)}. {_NOT_STARTED}",
        )

    name = _file_name(project_slug, version)
    folder = f"/target/{BACKUP_DIR}"
    part = f"{folder}/{name}.part"
    write = (
        f'set -e; mkdir -p "{folder}"; chown {remote_instance.UID}:{remote_instance.GID} "{folder}"; '
        f'chmod 700 "{folder}"; umask 077; cat > "{part}"'
    )
    dump_rc, write_rc, out = await _pipe(
        _compose(compose, "exec", "-T", service, "sh", "-c", _DUMP_SH),
        _tool(instance_dir, write, interactive=True),
        env,
        left(),
    )
    if dump_rc != 0 or write_rc != 0:
        await _run(_tool(instance_dir, f'rm -f "{part}"'), env, 60)
        return BackupOutcome(
            False, f"Záloha databázy zlyhala: {out[-300:] or f'exit {dump_rc}/{write_rc}'}. {_NOT_STARTED}"
        )

    final = f"{folder}/{name}"
    code, out = await _run(
        _tool(
            instance_dir,
            f'set -e; mv "{part}" "{final}"; chown {remote_instance.UID}:{remote_instance.GID} "{final}"; '
            f'chmod 600 "{final}"; stat -c %s "{final}"; sha256sum "{final}" | cut -d" " -f1',
        ),
        env,
        left(),
    )
    lines = out.splitlines()
    if code != 0 or len(lines) < 2 or not lines[-2].isdigit():
        return BackupOutcome(False, f"Zálohu sa nepodarilo uložiť: {out[-200:]}. {_NOT_STARTED}")
    dump_bytes, sha = int(lines[-2]), lines[-1]

    # Read back with the database's own pg_restore — the same version that would restore it.
    read_rc, list_rc, out = await _pipe(
        _tool(instance_dir, f'cat "{final}"', readonly=True),
        _compose(compose, "exec", "-T", service, "sh", "-c", _LIST_SH),
        env,
        left(),
    )
    entries = sum(1 for line in out.splitlines() if line.strip() and not line.startswith(";"))
    if read_rc != 0 or list_rc != 0 or entries == 0:
        await _run(_tool(instance_dir, f'rm -f "{final}"'), env, 60)
        return BackupOutcome(False, f"Záloha sa nedá prečítať, nasadenie by nemalo cestu späť. {_NOT_STARTED}")

    keep = settings.predeploy_backup_keep
    await _run(
        _tool(
            instance_dir,
            f'cd "{folder}" && ls -1t -- *.dump | tail -n +{keep + 1} | while read f; do rm -f -- "$f"; done',
        ),
        env,
        60,
    )
    path = f"{instance_dir}/{BACKUP_DIR}/{name}"
    return BackupOutcome(
        True,
        f"{BACKUP_SENTENCE} {path} ({_size(dump_bytes)}, {entries} položiek, sha256 {sha[:12]}…; "
        f"obnova: pg_restore --clean --if-exists v databázovom kontajneri inštalácie). Ponechaných je posledných "
        f"{keep} záloh.",
        path,
    )
