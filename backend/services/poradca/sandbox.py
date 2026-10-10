"""Kontajner jednej otázky Poradcu — zoznam pripojení JE záruka (``docs/specs/poradca.md`` §4.1).

Vzor je izolovaná stavba (:mod:`backend.services.build_sandbox`), nie Konzultácia: dočasný domov,
neprivilegovaný používateľ bez schopností, oplotená sieť, prihlásenie len premennou prostredia. Navyše oproti
stavbe, lebo Poradca len číta:

  * **Claude Code v obmedzenom režime** (``--restricted --tools Read,Grep,Glob``): nástroje, ktoré spúšťajú
    príkazy alebo kód, ``WebFetch`` aj ``WebSearch`` nie sú k dispozícii; nastavenia používateľa a projektu
    sa nenačítajú; súborové nástroje nesiahnu mimo pracovného priečinka — ani cez symbolický odkaz.
    Zmerané 05.10.2026 s CLI 2.1.289: v zozname nástrojov boli len ``Glob``, ``Grep``, ``Read`` a nástroje
    Poradcu; čítanie ``/etc/hostname`` priamo aj cez odkaz v projekte odmietlo;
  * **projekt len na čítanie** — zápis odmietne jadro systému, nie sľub v pokyne;
  * **súbory s tajomstvami prekryté prázdnym súborom** (``.env*``, kľúče, certifikáty, …) a rovnako každý
    súbor s viacerými pevnými odkazmi — pevný odkaz je druhé meno súboru, ktorý môže ležať kdekoľvek na tom
    istom disku, a obmedzený režim ho od obyčajného súboru nerozozná;
  * ``.git`` prekrytý prázdnym ``tmpfs`` — históriu podáva nástroj ``git_historia`` z backendu;
  * **žiadna Znalostná báza** — tú podáva nástroj s právami človeka, ktorý sa pýta; pripojený priečinok
    by ich obišiel;
  * **záznam rozhovoru** v priečinku TOHTO rozhovoru, pripojený tam, kam ho Claude Code ukladá podľa
    pracovného priečinka — záznamy agenta stavby sa nepripájajú vôbec (nález B3);
  * **snímky obrazovky, ktoré Manažér priložil k otázkam TOHTO rozhovoru** (DEV-52) — len na čítanie, mimo
    projektu, a obmedzenému režimu povolené prepínačom ``--add-dir`` (bez neho Read mimo projektu odmietne;
    zmerané 10.10.2026 s CLI 2.1.294 oboma smermi). Iný rozhovor ani projekt sa k nim nedostane.

Nástroje „zozadu" podáva backend cez unixový socket platný pre jednu otázku (:mod:`.mcp_server`);
v kontajneri ich sprostredkuje :mod:`.shim`. Sieť ostáva oplotená ako pri stavbe.

Keď sa kontajner nedá spustiť, otázka skončí chybou s dôvodom — **nikdy** neustúpi na beh mimo kontajnera.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from backend.config.settings import settings
from backend.services import build_db, build_sandbox, sandbox_paths
from backend.services.project_files import PRIVATE_DIR

logger = logging.getLogger(__name__)

_LABEL = "poradca sandbox"

#: Vstavané nástroje Poradcu — len čítanie. Všetko ostatné z neho robí obmedzený režim nedostupným.
BUILTIN_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")
#: Meno servera MCP; nástroje sa v Claude Code volajú ``mcp__poradca__<nástroj>``.
MCP_SERVER_NAME = "poradca"

#: Kde kontajner vidí priečinok so socketom tejto otázky.
CONTAINER_RUN_DIR = "/run/poradca"
SOCKET_NAME = "mcp.sock"
#: DEV-52 — kde kontajner vidí priložené snímky svojho rozhovoru (len na čítanie, mimo projektu).
CONTAINER_ATTACHMENTS_DIR = "/run/poradca-prilohy"
#: Prostredník stdio ↔ socket, v obraze backendu (iba štandardná knižnica Pythonu).
SHIM_PATH = "/app/backend/services/poradca/shim.py"

_HOME = "/home/andros"
_CLAUDE_DIR = f"{_HOME}/.claude"
_CLAUDE_BIN_DIR = "/home/andros/.local"
_USER = "1000:1000"

#: Premenné prostredia podané MENOM (hodnota nejde do argv, teda ani do ``ps``). Žiadny token GitHubu,
#: žiadna databáza — Poradca nič nezapisuje a „zozadu" mu podáva backend.
_PASSTHROUGH_ENV: tuple[str, ...] = ("CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_AUTOUPDATER", "LANG")

#: Mená súborov, ktoré nesú tajomstvá. Prekryjú sa prázdnym súborom, nech ležia v projekte kdekoľvek.
_SECRET_FILE_RE = re.compile(
    r"^(?:\.env(?:\..+)?"
    r"|.+\.(?:pem|key|p12|pfx|jks|keystore|kdbx)"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:[^.]*|.*(?<!\.pub))"
    r"|\.netrc|\.pgpass|\.git-credentials|credentials\.json)$",
    # ``.`` aj cez nový riadok: súbor ``.env.<nový riadok>…`` nesmie uniknúť prekrytiu len pre svoje meno.
    re.S,
)

#: Znaky, ktoré v zápise ``--mount`` dockera začínajú nové pole alebo záznam (čiarka, úvodzovky) — a riadiace
#: znaky. Súbor s takým menom by do pripojenia prekrytia vpašoval vlastný ``source=`` a docker by pod
#: priečinok projektu pripojil ľubovoľnú cestu hostiteľa (nález nezávislej previerky 05.10.2026). Takú cestu
#: preto Poradca nikdy neskladá — otázka zlyhá s menom súboru.
_MOUNT_UNSAFE = re.compile(r'[,"\x00-\x1f\x7f]')

#: Najdlhšia cesta unixového socketu, ktorú jadro Linuxu prijme (``sun_path`` má 108 bajtov vrátane nuly).
MAX_SOCKET_PATH = 107

#: Viac prekrytí než toto je nález, nie bežný projekt — otázka radšej zlyhá s dôvodom.
MAX_OVERLAYS = 2000

#: Strop pamäte a procesov kontajnera — otázka, ktorá len číta, nepotrebuje viac.
_MEMORY = "2g"
_PIDS = "512"


class PoradcaUnavailable(RuntimeError):
    """Kontajner Poradcu sa nedá spustiť — otázka zlyhá s dôvodom, nikdy sa nespustí bez neho."""


def data_dir() -> Path:
    """Priečinok Poradcu na hostiteľovi — backend ho má pripojený na tej istej ceste (identita)."""
    return Path(settings.poradca_data_dir)


def session_dir(conversation_id: UUID) -> Path:
    return data_dir() / "sessions" / str(conversation_id)


def attachments_dir(conversation_id: UUID) -> Path:
    """DEV-52 — snímky, ktoré Manažér priložil k otázkam rozhovoru. Vedľa záznamu, nie v ňom: záznam má kontajner
    pripojený na zápis, snímky len na čítanie."""
    return data_dir() / "attachments" / str(conversation_id)


#: Čo vymazanie rozhovoru presúva do koša — záznam sedenia a priložené snímky, každé pod svojím menom.
_TRASHED = (("session", session_dir), ("attachments", attachments_dir))


def run_dir(token: str) -> Path:
    return data_dir() / "run" / token


def empty_file() -> Path:
    return data_dir() / "empty"


def trash_dir() -> Path:
    return data_dir() / "trash"


def move_to_trash(conversation_id: UUID) -> Optional[Path]:
    """Presunie záznam vymazaného rozhovoru a jeho priložené snímky do koša a vráti, kam; ``None``, keď nie je nič.

    ``os.rename`` v rámci jedného disku (kôš leží vedľa ``sessions/`` a ``attachments/``) je nedeliteľný: každá
    časť je buď celá preč, alebo celá na mieste — a keď druhá časť nejde, prvá sa vráti, takže rozhovor nikdy
    neostane napoly. Vymazanie to robí pred zmenou databázy a pri jej zlyhaní všetko vráti
    (:func:`restore_from_trash`); samotné mazanie súborov (:func:`discard`) príde až po nej, mimo zámku.
    """
    present = [(name, where(conversation_id)) for name, where in _TRASHED if where(conversation_id).exists()]
    if not present:
        return None
    trash_dir().mkdir(parents=True, exist_ok=True)
    target = trash_dir() / f"{conversation_id}-{uuid4().hex}"
    target.mkdir()
    moved: list[tuple[str, Path]] = []
    try:
        for name, source in present:
            os.rename(source, target / name)
            moved.append((name, source))
    except OSError:
        for name, source in reversed(moved):
            os.rename(target / name, source)
        target.rmdir()
        raise
    return target


def restore_from_trash(trashed: Path, conversation_id: UUID) -> None:
    for name, where in _TRASHED:
        if (trashed / name).exists():
            os.rename(trashed / name, where(conversation_id))
    trashed.rmdir()


def discard(trashed: Path) -> None:
    """Zmaže záznam z koša. Čo nejde (napr. kontajner, ktorý prežil reštart, doň ešte píše), dozmaže
    :func:`sweep_trash` pri ďalšom štarte — rozhovor je vymazaný tak či tak."""
    shutil.rmtree(trashed, ignore_errors=True)
    if trashed.exists():
        logger.warning("%s: %s sa nepodarilo celý zmazať — dozmaže ho štart backendu", _LABEL, trashed)


def sweep_trash() -> int:
    """Pri štarte backendu: dozmaže, čo v koši ostalo. Vráti, koľko záznamov zmizlo. Nikdy nevyhodí."""
    base = trash_dir()
    if not base.is_dir():
        return 0
    removed = 0
    for child in base.iterdir():
        shutil.rmtree(child, ignore_errors=True)
        removed += 0 if child.exists() else 1
    return removed


def container_name(project_slug: str, token: str) -> str:
    return f"nex-poradca-{project_slug}-{token}"


def network_name(project_slug: str, token: str) -> str:
    return f"nex-poradca-{project_slug}-{token}"


def project_dirs(project_slug: str) -> tuple[str, str]:
    """``(cesta v kontajneri, cesta na hostiteľovi)`` — slug overený, cesta neunikne z ``/opt/projects``."""
    try:
        sandbox_paths.validate_project_slug(project_slug, sandbox=_LABEL)
        from backend.services.claude_agent import PROJECTS_ROOT

        container_dir = str(PROJECTS_ROOT / project_slug)
        prefixes: sandbox_paths.HostPrefixes = (("/opt/projects", "/opt/projects"),)
        host_dir = sandbox_paths.container_to_host(container_dir, prefixes, sandbox=_LABEL)
        sandbox_paths.assert_host_source_contained(host_dir, prefixes, sandbox=_LABEL)
    except sandbox_paths.SandboxPathError as exc:
        raise PoradcaUnavailable(str(exc)) from exc
    return container_dir, host_dir


def overlay_paths(project_dir: str) -> list[str]:
    """Cesty v projekte (relatívne), ktoré sa prekryjú prázdnym súborom.

    Súbor s menom tajomstva, každý obyčajný súbor s viac než jedným pevným odkazom a každý súbor v ``private/``
    (DEV-44 — čo Manažér priložil agentovi). Symbolické odkazy sa
    neprekrývajú: obmedzený režim ich sám rozlúšti a čítanie mimo projektu odmietne (zmerané), kým prekrytie
    odkazu by docker rozlúštil vnútri kontajnera a mohol by ho namieriť mimo projektu. ``.git`` sa
    neprechádza — celý sa prekrýva prázdnym ``tmpfs``.

    Raises:
        PoradcaUnavailable: viac prekrytí než :data:`MAX_OVERLAYS`.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project_dir, followlinks=False):
        if dirpath == project_dir and ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(full, project_dir)
            # DEV-44: what the Manažér attached for the agent (a real e-mail with personal codes) — Poradca
            # sees the name, never the content, so the codes cannot end up quoted in the conversation.
            attached = rel.split(os.sep, 1)[0] == PRIVATE_DIR
            if _SECRET_FILE_RE.match(name) or st.st_nlink > 1 or attached:
                if _MOUNT_UNSAFE.search(rel):
                    raise PoradcaUnavailable(
                        f"{_LABEL}: súbor s tajomstvom alebo pevným odkazom má v ceste čiarku, úvodzovky či riadiaci "
                        f"znak ({rel!r}) — také meno sa nedá bezpečne prekryť. Premenuj ho, potom sa Poradca spustí."
                    )
                found.append(rel)
                if len(found) > MAX_OVERLAYS:
                    raise PoradcaUnavailable(
                        f"{_LABEL}: projekt má viac než {MAX_OVERLAYS} súborov na prekrytie (tajomstvá alebo "
                        "pevné odkazy) — to nie je bežný projekt; over ho skôr, než sa Poradca spustí"
                    )
    return sorted(found)


def prepare(conversation_id: UUID, token: str) -> tuple[Path, Path]:
    """Vytvorí priečinok záznamu rozhovoru a priečinok socketu otázky; vráti ich cesty.

    Vlastníkom je používateľ kontajnera (1000), lebo Claude Code v ňom zapisuje záznam. Prázdny súbor na
    prekrytia sa vytvorí raz, len na čítanie.

    Raises:
        PoradcaUnavailable: priečinok Poradcu chýba alebo sa doň nedá zapisovať.
    """
    base = data_dir()
    socket_path = run_dir(token) / SOCKET_NAME
    if len(str(socket_path).encode()) > MAX_SOCKET_PATH:
        raise PoradcaUnavailable(
            f"{_LABEL}: cesta socketu {socket_path} je dlhšia než {MAX_SOCKET_PATH} bajtov, ktoré unixový socket "
            "dovolí — priečinok Poradcu musí mať kratšiu cestu"
        )
    if not base.is_dir():
        raise PoradcaUnavailable(
            f"{_LABEL}: priečinok {base} neexistuje — v PROD compose kokpitu chýba jeho pripojenie "
            "(rovnaká cesta na hostiteľovi aj v backende)"
        )
    try:
        sessions = session_dir(conversation_id)
        sessions.mkdir(parents=True, exist_ok=True)
        run = run_dir(token)
        run.mkdir(parents=True, exist_ok=True)
        empty = empty_file()
        if not empty.exists():
            empty.write_bytes(b"")
        os.chmod(empty, 0o444)
        for path in (sessions, run):
            os.chown(path, 1000, 1000)
        for child in sessions.rglob("*"):
            os.lchown(child, 1000, 1000)
    except OSError as exc:
        raise PoradcaUnavailable(f"{_LABEL}: nedá sa pripraviť priečinok otázky ({exc})") from exc
    return sessions, run


@dataclass(frozen=True)
class ClaudeCall:
    """Čo sa pýta a s akým modelom — všetko, čím sa líšia dve otázky."""

    prompt: str
    claude_session_id: UUID
    #: Prvá otázka rozhovoru nesie chartu a zakladá sedenie; ďalšie ho obnovia (``--resume``).
    charter_text: Optional[str]
    model: Optional[str]
    effort: Optional[str]
    #: DEV-52 — rozhovor má priložené snímky: ich priečinok sa pripojí a obmedzený režim ho smie čítať.
    attachments: bool = False


def claude_argv(call: ClaudeCall) -> list[str]:
    """Prepínače ``claude`` (bez mena programu — ten dodá ``--entrypoint``)."""
    mcp = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "stdio",
                "command": "python3",
                "args": [SHIM_PATH, f"{CONTAINER_RUN_DIR}/{SOCKET_NAME}"],
            }
        }
    }
    args = [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--tools",
        ",".join(BUILTIN_TOOLS),
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp),
        "--allowedTools",
        f"mcp__{MCP_SERVER_NAME}",
        # ``--add-dir`` berie viac priečinkov — za ním preto vždy nasleduje ďalší prepínač, nikdy text otázky.
        *(["--add-dir", CONTAINER_ATTACHMENTS_DIR] if call.attachments else []),
        "--permission-mode",
        "dontAsk",
    ]
    if call.charter_text is not None:
        args += ["--session-id", str(call.claude_session_id), "--append-system-prompt", call.charter_text]
    else:
        args += ["--resume", str(call.claude_session_id)]
    if call.model:
        args += ["--model", call.model]
    if call.effort:
        args += ["--effort", call.effort]
    args.append(call.prompt)
    return args


def _assert_mount_safe(*parts: str) -> None:
    """Druhá poistka: nič, čo sa skladá do ``--mount``, nesmie niesť znak, ktorý v ňom začína nové pole."""
    for part in parts:
        if _MOUNT_UNSAFE.search(part):
            raise PoradcaUnavailable(f"{_LABEL}: cesta {part!r} sa nedá bezpečne pripojiť (čiarka/úvodzovky)")


def run_argv(
    *,
    project_slug: str,
    conversation_id: UUID,
    token: str,
    network: Optional[str],
    call: ClaudeCall,
    overlays: list[str],
) -> list[str]:
    """Celý ``docker run`` jednej otázky. Čo tu nie je, Poradca nevidí.

    PRÍTOMNÉ: dočasný domov, záznam TOHTO rozhovoru, projekt len na čítanie s prekrytiami, ``.git`` ako
    prázdny ``tmpfs``, binárka ``claude`` len na čítanie, priečinok socketu tejto otázky len na čítanie a — keď
    rozhovor nejaké má — priložené snímky TOHTO rozhovoru len na čítanie (DEV-52).
    NEPRÍTOMNÉ (stráži skúška): ``docker.sock``, ``/opt/customers``, ``/opt/uat``, ``/opt/infra``,
    Znalostná báza, trezor prístupov, zdieľaný ``~/.claude``, záznamy agenta stavby.
    """
    container_dir, host_dir = project_dirs(project_slug)
    _assert_mount_safe(
        host_dir, container_dir, str(session_dir(conversation_id)), str(run_dir(token)), str(empty_file())
    )
    _assert_mount_safe(*overlays)
    container_session_dir = f"{_CLAUDE_DIR}/projects/{build_sandbox.session_dir_name(container_dir)}"
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name(project_slug, token),
        # Značka stavby: kontajner, ktorý prežije reštart backendu (otázka beží ďalej, nikto ju nečíta, píše
        # do záznamu rozhovoru), pri štarte odprace ``build_db.reap_orphans`` — rovnako ako jeho sieť.
        "--label",
        f"{build_db.OWNER_LABEL}=poradca",
        "--user",
        _USER,
        *(["--network", network] if network else []),
        "--no-healthcheck",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--memory",
        _MEMORY,
        "--pids-limit",
        _PIDS,
        "--mount",
        f"type=tmpfs,destination={_HOME},tmpfs-mode=0777",
        "--mount",
        "type=tmpfs,destination=/tmp,tmpfs-mode=1777",
        "--mount",
        f"type=bind,source={session_dir(conversation_id)},target={container_session_dir}",
        "--mount",
        f"type=bind,source={host_dir},target={container_dir},readonly",
    ]
    if os.path.isdir(os.path.join(host_dir, ".git")):
        argv += ["--mount", f"type=tmpfs,destination={container_dir}/.git,tmpfs-mode=0555"]
    for rel in overlays:
        argv += ["--mount", f"type=bind,source={empty_file()},target={container_dir}/{rel},readonly"]
    if call.attachments:
        attached = attachments_dir(conversation_id)
        _assert_mount_safe(str(attached))
        argv += ["--mount", f"type=bind,source={attached},target={CONTAINER_ATTACHMENTS_DIR},readonly"]
    argv += [
        "--mount",
        f"type=bind,source={_CLAUDE_BIN_DIR},target={_CLAUDE_BIN_DIR},readonly",
        "--mount",
        f"type=bind,source={run_dir(token)},target={CONTAINER_RUN_DIR},readonly",
        "-e",
        f"HOME={_HOME}",
        "-e",
        f"CLAUDE_CONFIG_DIR={_CLAUDE_DIR}",
    ]
    for name in _PASSTHROUGH_ENV:
        argv += ["-e", name]
    argv += ["-w", container_dir, "--entrypoint", "claude", build_sandbox.sandbox_image(), *claude_argv(call)]
    return argv
