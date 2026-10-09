"""Nástroje Poradcu na pohľad „zozadu" (ICCINT-167, návrh §4.2).

Všetko podáva BACKEND: meno kontajnera, priečinok ani databázu neurčuje agent, ale kokpit z projektu
rozhovoru. Agent smie vybrať len z toho, čo mu kokpit sám ponúkne (napr. meno kontajnera zo zoznamu
``kontajnery``); čokoľvek iné nástroj odmietne vetou. Výstup každého nástroja prejde filtrom tajomstiev
a stropom dĺžky v :mod:`.mcp_server` — tu sa len zbiera.

PROD sa nevracia nikdy: Poradca ostrú prevádzku zákazníkov nevidí (rozhodnutie Directora 05.10.2026).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services.poradca.context import UatInstallation, uat_installations
from backend.services.poradca.mcp_server import Tool, ToolError

#: Mená tlačidiel Riadiaceho centra podľa akcie, ktorú kokpit ponúka (``available_actions``). Poradca
#: radí tlačidlom, ktoré človek na obrazovke vidí — nie menom v kóde. Stráži ich skúška: akcia, ktorú
#: frontend pozná a ktorá tu chýba, zčervená.
ACTION_LABELS: dict[str, str] = {
    "start": "Spustiť tvorbu špecifikácie",
    "approve_spec": "Schváliť špecifikáciu",
    "zostav_plan": "Zostaviť plán",
    "spustit_stavbu": "Spustiť stavbu",
    "spustit_vizual": "Spustiť vizuál",
    "schvalit": "Schváliť",
    "uprav": "Uprav",
    "pokracovat": "Pokračovať",
    "verdict": "verdikt Audítora (PASS / FAIL)",
    "ask": "otázka pre AI Agenta",
    "answer": "Odpovedať (agent sa pýta)",
    "pause": "Pozastaviť",
    "decide": "výber na karte rozhodnutia",
    "overit_znovu": "Over znova",
    "overit_bez_opravy": "Znova overiť bez opravy",
    "skontrolovat": "Skontrolovať",
    "hotovo": "Hotovo",
    "na_riadnu_verziu": "Preniesť do riadnej verzie",
    "nahlasit_znova": "Nahlásiť znova",
    "zopakovat_konzultaciu": "Zopakovať konzultáciu",
}

_MESSAGES_LIMIT = 50
_MESSAGE_CHARS = 1500
_GIT_LOG_MAX = 100
_GIT_SHOW_LINES = 2000
_AGENT_STEPS_MAX = 200
_LOG_LINES_MAX = 500
_CI_LOG_LINES = 300
_KB_RESULTS = 10
_KB_DOC_CHARS = 40_000
_DB_ROWS = 200
_DB_TIMEOUT_S = 10
_CMD_TIMEOUT_S = 30

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_UAT_DB_ROLE = "poradca_ro"


def _schema(properties: Optional[dict] = None, required: Optional[list[str]] = None) -> dict:
    out: dict = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


async def _run(argv: list[str], *, cwd: Optional[str] = None, timeout: int = _CMD_TIMEOUT_S) -> tuple[int, str]:
    """Spustí príkaz BEZ shellu (pole argumentov) a vráti ``(kód, stdout+stderr)``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError as exc:
        raise ToolError(f"Program {argv[0]} nie je v kokpite k dispozícii.") from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise ToolError(f"Príkaz nedobehol do {timeout} s.") from exc
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _tail(text: str, lines: int) -> str:
    rows = text.splitlines()
    if len(rows) <= lines:
        return text
    return f"… (vynechaných {len(rows) - lines} riadkov)\n" + "\n".join(rows[-lines:])


def _int_arg(args: dict, key: str, default: int, maximum: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolError(f"Argument {key} musí byť kladné celé číslo.")
    return min(value, maximum)


def _cards_lines(db: Session, version_id: UUID) -> list[str]:
    """DEV-28: the Decision Cards as the Manažér sees them, so Poradca names an option word for word.

    DEV-30: everything the card shows — the option descriptions, the technical detail and the rationale too.
    They carry the fix the agent already planned; Poradca adds to it only what is missing.

    The cards live in the consultation message beside its text (``payload.consultation``) and the decisions in
    the Manažér's answers (``payload.consultation_decision``); the text alone named none of the options — on
    NEX Inbox 1.7.0 the consultation's 332 characters carried 0 of its 20 option labels, and Poradca could only
    describe a choice („zvoľ opravu, pri ktorej …"). Read through the engine's own functions — the same ones
    the cards are built and decided from — never through a copy of their rules."""
    from backend.services.orchestrator import _consultation_answers, _latest_consultation

    latest = _latest_consultation(db, version_id)
    if latest is None:
        return []
    consultation, seq = latest
    answers = _consultation_answers(db, version_id, seq)
    decisions = consultation.get("decisions") or []
    current = next((i for i, d in enumerate(decisions) if d.get("key") not in answers), None)
    rnd, rmax = consultation.get("round"), consultation.get("round_max")
    head = "Karty rozhodnutí (konzultácia" + (
        f", kolo {rnd} z {rmax}" if rnd and rmax else f", kolo {rnd}" if rnd else ""
    )
    head += (
        f") — na rade je karta {current + 1} z {len(decisions)}:"
        if current is not None
        else ") — všetky sú rozhodnuté:"
    )
    out = ["", head]
    for i, d in enumerate(decisions):
        answer = answers.get(d.get("key"))
        mark = "rozhodnutá" if answer else ("NA RADE" if i == current else "čaká")
        out.append(f"Karta {i + 1} [{mark}]: {d.get('question', '')}")
        if d.get("explanation"):
            out.append(f"  Vysvetlenie: {d['explanation']}")
        if d.get("origin") == "dosledok" and d.get("origin_of"):
            out.append(f"  Vyplýva z rozhodnutia o: {d['origin_of']}")
        # DEV-34: the cards this choice hangs together with, as the card shows them to the Manažér.
        index = {x.get("key"): n for n, x in enumerate(decisions)}
        for rel in d.get("related") or []:
            other = index.get(rel.get("key"))
            if other is None or rel.get("key") == d.get("key"):
                continue
            why = (rel.get("why") or "").strip()
            out.append(
                f"  Súvisí s kartou {other + 1} („{decisions[other].get('question', '')}“)"
                + (f": {why}" if why else ".")
            )
        # DEV-30: the plan the agent already wrote — without it Poradca rewrote the plan in every instruction.
        if d.get("technical_detail"):
            out.append(f"  Technický detail: {d['technical_detail']}")
        for option in d.get("options") or []:
            out.append(
                f"  možnosť „{option.get('label', '')}“"
                + (" (odporúčané)" if option.get("recommended") else "")
                + (f" — {option['detail']}" if option.get("detail") else "")
            )
        if d.get("rationale"):
            out.append(f"  Zdôvodnenie odporúčania: {d['rationale']}")
        if d.get("allow_free_text"):
            out.append("  dá sa napísať aj vlastná odpoveď (Iná odpoveď)")
        if answer:
            note = (answer.get("note") or "").strip()
            if len(note) > _MESSAGE_CHARS:
                note = note[:_MESSAGE_CHARS] + " …"
            out.append(
                f"  Manažér zvolil „{answer.get('label', '')}“" + (f"; pokyn pre AI partnera: {note}" if note else "")
            )
    return out


def _vizual_checklist_lines(checklists: list) -> list[str]:
    """DEV-36: what the Manažér was asked to check in the Vizuál, as his screen shows it — so when he asks
    Poradca to check the Vizuál too, Poradca walks the same items, links included."""
    out: list[str] = []
    for checklist in checklists:
        label = "prvý návrh" if checklist.round == "first" else "po zmene"
        out += ["", f"Čo má Manažér vo Vizuáli skontrolovať ({label}):"]
        for n, item in enumerate(checklist.items, start=1):
            out.append(f"{n}. {item.screen} — {item.action} → {item.expected}" + (f" ({item.url})" if item.url else ""))
        if checklist.not_verifiable:
            out.append("Vo Vizuáli sa overiť nedá: " + "; ".join(checklist.not_verifiable))
    return out


class PoradcaTools:
    """Nástroje jednej otázky — viazané na projekt, verziu rozhovoru a človeka, ktorý sa pýta."""

    def __init__(self, *, project_id: UUID, version_id: Optional[UUID], user_id: UUID) -> None:
        self.project_id = project_id
        self.version_id = version_id
        self.user_id = user_id

    # ── pomocné ────────────────────────────────────────────────────────────────

    def _project(self, db: Session) -> Project:
        project = db.get(Project, self.project_id)
        if project is None:
            raise ToolError("Projekt rozhovoru už neexistuje.")
        return project

    def _project_dir(self, db: Session) -> str:
        from backend.services.claude_agent import PROJECTS_ROOT

        return str(PROJECTS_ROOT / self._project(db).slug)

    def _version(self, db: Session, number: Optional[str]) -> Version:
        project = self._project(db)
        if number:
            version = db.execute(
                select(Version).where(Version.project_id == project.id, Version.version_number == number)
            ).scalar_one_or_none()
            if version is None:
                raise ToolError(f"Verzia {number} v projekte {project.slug} neexistuje.")
            return version
        if self.version_id is not None:
            version = db.get(Version, self.version_id)
            if version is not None:
                return version
        version = db.execute(
            select(Version).where(Version.project_id == project.id).order_by(Version.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if version is None:
            raise ToolError("Projekt nemá žiadnu verziu.")
        return version

    # ── stavba ─────────────────────────────────────────────────────────────────

    async def stavba(self, args: dict) -> str:
        from backend.api.routes.pipeline import _board

        with SessionLocal() as db:
            version = self._version(db, args.get("verzia"))
            board = _board(db, version.id, limit=_MESSAGES_LIMIT)
            state = board.state
            lines = [f"Verzia {version.version_number}."]
            if state is None:
                lines.append("Stavba tejto verzie ešte nezačala.")
                return "\n".join(lines)
            lines.append(
                f"Fáza: {state.current_stage}; stav: {state.status}; dôvod zastavenia: {state.block_reason or '—'}; "
                f"čo ďalej: {state.next_action or '—'}."
            )
            actions = board.available_actions or []
            if actions:
                lines.append(
                    "Tlačidlá, ktoré Manažér v Riadiacom centre práve vidí: "
                    + "; ".join(f"„{ACTION_LABELS.get(a, a)}“ ({a})" for a in actions)
                    + "."
                )
            else:
                lines.append("Manažér teraz v Riadiacom centre nevidí žiadne tlačidlo stavby.")
            if "decide" in actions:
                lines.extend(_cards_lines(db, version.id))
            if state.current_stage == "vizual":
                lines.extend(_vizual_checklist_lines(board.vizual_checklists))
            lines.append(f"\nPosledných {len(board.recent_messages or [])} správ rozhovoru stavby (najstaršia prvá):")
            for msg in board.recent_messages or []:
                text = (msg.content or "").strip()
                if len(text) > _MESSAGE_CHARS:
                    text = text[:_MESSAGE_CHARS] + " …"
                when = msg.created_at.strftime("%d.%m. %H:%M") if msg.created_at else ""
                lines.append(f"— [{when}] {msg.author} → {msg.recipient} ({msg.kind}, fáza {msg.stage}):\n{text}")
            return "\n".join(lines)

    async def plan_uloh(self, args: dict) -> str:
        with SessionLocal() as db:
            version = self._version(db, args.get("verzia"))
            epics = db.execute(select(Epic).where(Epic.version_id == version.id).order_by(Epic.number)).scalars().all()
            if not epics:
                return f"Verzia {version.version_number} nemá plán úloh."
            lines = [f"Plán úloh verzie {version.version_number}:"]
            for epic in epics:
                lines.append(f"EPIC {epic.number}. {epic.title} [{epic.status}]")
                feats = db.execute(select(Feat).where(Feat.epic_id == epic.id).order_by(Feat.number)).scalars().all()
                for feat in feats:
                    lines.append(f"  FEAT {epic.number}.{feat.number} {feat.title} [{feat.status}]")
                    tasks = (
                        db.execute(select(Task).where(Task.feat_id == feat.id).order_by(Task.number)).scalars().all()
                    )
                    for task in tasks:
                        lines.append(
                            f"    úloha {epic.number}.{feat.number}.{task.number} {task.title} [{task.status}]"
                        )
            return "\n".join(lines)

    # ── git ────────────────────────────────────────────────────────────────────

    def _relative_path(self, project_dir: str, raw: Any) -> Optional[str]:
        if raw in (None, ""):
            return None
        if not isinstance(raw, str):
            raise ToolError("Cesta musí byť text.")
        full = os.path.normpath(os.path.join(project_dir, raw))
        if not (full == project_dir or full.startswith(project_dir + os.sep)):
            raise ToolError("Cesta musí byť v projekte.")
        return os.path.relpath(full, project_dir)

    async def git_historia(self, args: dict) -> str:
        count = _int_arg(args, "pocet", 30, _GIT_LOG_MAX)
        with SessionLocal() as db:
            project_dir = self._project_dir(db)
        rel = self._relative_path(project_dir, args.get("cesta"))
        argv = [
            "git",
            "-C",
            project_dir,
            "log",
            f"-n{count}",
            "--no-color",
            "--date=format:%d.%m.%Y %H:%M",
            "--format=%h  %ad  %an  %s",
        ]
        if rel:
            argv += ["--", rel]
        code, out = await _run(argv)
        if code != 0:
            raise ToolError("História zmien sa nedá prečítať.")
        return out.strip() or "Žiadne zmeny."

    async def git_zmena(self, args: dict) -> str:
        commit = args.get("commit")
        if not isinstance(commit, str) or not _SHA_RE.match(commit):
            raise ToolError("Commit sa zadáva ako jeho číslo (7 až 40 znakov 0-9a-f).")
        with SessionLocal() as db:
            project_dir = self._project_dir(db)
        code, out = await _run(
            [
                "git",
                "-C",
                project_dir,
                "show",
                "--no-color",
                "--stat",
                "--patch",
                "--format=fuller",
                "--end-of-options",
                commit,
                "--",
            ]
        )
        if code != 0:
            raise ToolError(f"Commit {commit} sa v projekte nenašiel.")
        rows = out.splitlines()
        if len(rows) > _GIT_SHOW_LINES:
            out = "\n".join(rows[:_GIT_SHOW_LINES]) + f"\n… (skrátené, spolu {len(rows)} riadkov)"
        return out

    # ── kroky agenta stavby ─────────────────────────────────────────────────────

    async def zaznam_agenta(self, args: dict) -> str:
        from backend.services import build_sandbox

        count = _int_arg(args, "pocet", 80, _AGENT_STEPS_MAX)
        with SessionLocal() as db:
            project_dir = self._project_dir(db)
        transcripts = Path(build_sandbox._CLAUDE_HOME_DIR) / "projects" / build_sandbox.session_dir_name(project_dir)
        return await asyncio.to_thread(_agent_steps, transcripts, count, project_dir)

    # ── kontajnery a logy ───────────────────────────────────────────────────────

    async def _containers(self) -> list[dict]:
        with SessionLocal() as db:
            project = self._project(db)
            installations = uat_installations(db, project)
            slug = project.slug
        code, out = await _run(["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"])
        if code != 0:
            raise ToolError("Zoznam kontajnerov sa nedá prečítať.")
        return _project_containers(out, slug, installations)

    async def kontajnery(self, args: dict) -> str:
        rows = await self._containers()
        if not rows:
            return "Projekt nemá žiadny kontajner (náhľad Vizuálu, databázu stavby ani inštaláciu UAT)."
        return "\n".join(f"{r['name']}  [{r['kind']}]  {r['state']} — {r['status']}  ({r['image']})" for r in rows)

    async def logy(self, args: dict) -> str:
        name = args.get("kontajner")
        lines = _int_arg(args, "riadkov", 200, _LOG_LINES_MAX)
        allowed = {r["name"] for r in await self._containers()}
        if not isinstance(name, str) or name not in allowed:
            raise ToolError("Log sa dá čítať len z kontajnera, ktorý vráti nástroj kontajnery.")
        code, out = await _run(["docker", "logs", "--tail", str(lines), "--timestamps", name])
        if code != 0:
            raise ToolError(f"Log kontajnera {name} sa nedá prečítať.")
        return out.strip() or "Log je prázdny."

    # ── CI ─────────────────────────────────────────────────────────────────────

    async def ci(self, args: dict) -> str:
        from backend.services import ci_status

        with SessionLocal() as db:
            project_dir = self._project_dir(db)
        snap = await ci_status.snapshot(Path(project_dir))
        lines = [f"Zostavenie (CI) pre commit {snap.sha or '?'}: {snap.state} — {snap.detail}"]
        if snap.state != "red" or not snap.sha:
            return "\n".join(lines)
        code, remote = await _run(["git", "-C", project_dir, "config", "--get", "remote.origin.url"])
        repo = ci_status._repo_z_remote(remote) if code == 0 else None
        if not repo:
            return "\n".join(lines)
        rows, chyba = await ci_status._behy_z_githubu(Path(project_dir), repo, snap.sha)
        failed = ci_status.prvy_zlyhany(rows or []) if not chyba else None
        if failed is None:
            return "\n".join(lines)
        code, log = await _run(
            ["gh", "run", "view", str(failed.get("databaseId")), "-R", repo, "--log-failed"], timeout=60
        )
        if code != 0:
            lines.append("Log zlyhaného kroku sa nepodarilo stiahnuť.")
        else:
            lines.append(
                f"\nKoniec logu zlyhaného kroku ({ci_status.pomenuj_beh(failed)}):\n{_tail(log, _CI_LOG_LINES)}"
            )
        return "\n".join(lines)

    # ── Znalostná báza ──────────────────────────────────────────────────────────

    async def znalostna_baza(self, args: dict) -> str:
        from backend.rag import reader
        from backend.utils.kb_access import is_path_allowed

        query = args.get("dotaz")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("Zadaj, čo hľadáš.")
        try:
            hits = await asyncio.to_thread(reader.search, tenant="icc", query=query, limit=_KB_RESULTS * 3)
        except reader.RagUnavailableError as exc:
            raise ToolError("Vyhľadávanie v Znalostnej báze teraz nie je dostupné.") from exc
        with SessionLocal() as db:
            user = db.get(User, self.user_id)
            allowed = [h for h in hits if user is not None and is_path_allowed(h["source_file"], user, db)]
        allowed = allowed[:_KB_RESULTS]
        if not allowed:
            return "V Znalostnej báze som k tomu nič nenašiel (alebo to nie je v tvojich právach)."
        return "\n\n".join(f"{h['source_file']} (zhoda {h['score']}):\n{h['snippet']}" for h in allowed)

    async def znalostna_baza_dokument(self, args: dict) -> str:
        from backend.utils.kb_access import is_path_allowed

        raw = args.get("cesta")
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError("Zadaj cestu dokumentu tak, ako ju vrátilo vyhľadávanie.")
        root = os.path.realpath(settings.knowledge_base_path)
        full = os.path.realpath(os.path.join(root, raw.lstrip("/")))
        if not full.startswith(root + os.sep):
            raise ToolError("Dokument musí byť v Znalostnej báze.")
        rel = os.path.relpath(full, root)
        with SessionLocal() as db:
            user = db.get(User, self.user_id)
            if user is None or not is_path_allowed(rel, user, db):
                raise ToolError("Tento dokument nie je v právach človeka, ktorý sa pýta.")
        try:
            text = Path(full).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError("Dokument sa nedá prečítať.") from exc
        if len(text) > _KB_DOC_CHARS:
            text = text[:_KB_DOC_CHARS] + "\n… (skrátené)"
        return text

    # ── databáza UAT ─────────────────────────────────────────────────────────────

    async def databaza_uat(self, args: dict) -> str:
        from backend.services.poradca import uat_db

        query = args.get("dotaz")
        if not isinstance(query, str):
            raise ToolError("Dotaz musí byť text.")
        problem = uat_db.query_problem(query)
        if problem:
            raise ToolError(problem)
        customer = args.get("zakaznik")
        with SessionLocal() as db:
            installations = uat_installations(db, self._project(db))
        if not installations:
            raise ToolError("Projekt nemá žiadnu inštaláciu UAT.")
        if customer is None and len(installations) == 1:
            chosen = installations[0]
        else:
            matches = [i for i in installations if i.customer_slug == customer]
            if not matches:
                names = ", ".join(i.customer_slug for i in installations)
                raise ToolError(f"Vyber zákazníka argumentom zakaznik: {names}.")
            chosen = matches[0]
        return await uat_db.run_query(chosen, query, rows=_DB_ROWS, timeout_s=_DB_TIMEOUT_S)


def _agent_steps(transcripts: Path, count: int, project_dir: str) -> str:
    """Posledné kroky agenta stavby zo záznamov Claude Code — nástroj, cieľ a chyba; obsah súborov nie."""
    if not transcripts.is_dir():
        return "Agent stavby v tomto projekte ešte nepracoval (záznam neexistuje)."
    files = sorted(transcripts.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    steps: deque[str] = deque(maxlen=count)
    pending: dict[str, int] = {}
    for path in files[-5:]:
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for raw in handle:
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                when = str(entry.get("timestamp") or "")[:16].replace("T", " ")
                message = entry.get("message") or {}
                blocks = message.get("content") if isinstance(message, dict) else None
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        steps.append(
                            f"[{when}] {block.get('name')}: {_step_target(block.get('input') or {}, project_dir)}"
                        )
                        pending[str(block.get("id"))] = len(steps)
                    elif block.get("type") == "tool_result" and block.get("is_error"):
                        content = block.get("content")
                        if isinstance(content, list):
                            content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
                        steps.append(f"    ✗ chyba: {str(content or '').strip()[:300]}")
    if not steps:
        return "V zázname agenta stavby nie sú žiadne kroky."
    return f"Posledných {len(steps)} krokov agenta stavby (najstarší prvý):\n" + "\n".join(steps)


def _step_target(args: dict, project_dir: str) -> str:
    for key in ("file_path", "path", "notebook_path"):
        if isinstance(args.get(key), str):
            value = args[key]
            return os.path.relpath(value, project_dir) if value.startswith(project_dir) else value
    if isinstance(args.get("command"), str):
        return args["command"].strip().splitlines()[0][:200] if args["command"].strip() else ""
    if isinstance(args.get("pattern"), str):
        return f"hľadá „{args['pattern'][:100]}“"
    if isinstance(args.get("description"), str):
        return args["description"][:200]
    return ""


def _project_containers(docker_ps_json: str, slug: str, installations: list[UatInstallation]) -> list[dict]:
    """Kontajnery projektu z ``docker ps --format {{json .}}``: náhľad Vizuálu, stavba, inštalácie UAT."""
    by_dir = {str(i.directory): i for i in installations}
    out: list[dict] = []
    for line in docker_ps_json.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(row.get("Names") or "")
        labels = str(row.get("Labels") or "")
        kind = None
        if name == f"vizual-{slug}":
            kind = "náhľad Vizuálu"
        elif name.startswith(f"nex-build-db-{slug}-"):
            kind = "databáza stavby"
        elif name.startswith(f"nex-build-{slug}-"):
            kind = "ťah agenta stavby"
        else:
            for part in labels.split(","):
                key, _, value = part.partition("=")
                if key == "com.docker.compose.project.working_dir" and value in by_dir:
                    kind = f"UAT {by_dir[value].customer_slug}"
                    break
        if kind:
            out.append(
                {
                    "name": name,
                    "kind": kind,
                    "state": row.get("State", ""),
                    "status": row.get("Status", ""),
                    "image": row.get("Image", ""),
                }
            )
    return sorted(out, key=lambda r: (r["kind"], r["name"]))


def build_tools(*, project_id: UUID, version_id: Optional[UUID], user_id: UUID) -> list[Tool]:
    """Nástroje jednej otázky. Popisy čítá agent — hovoria, čo nástroj vráti a čo nie."""
    t = PoradcaTools(project_id=project_id, version_id=version_id, user_id=user_id)
    verzia = {"verzia": {"type": "string", "description": "Číslo verzie, napr. 1.2.0; bez neho verzia rozhovoru."}}
    return [
        Tool(
            "stavba",
            "Stav stavby verzie: fáza, stav, dôvod zastavenia, čo ďalej, tlačidlá, ktoré Manažér v Riadiacom "
            "centre práve vidí, a posledných 50 správ rozhovoru Manažéra s agentom.",
            _schema(verzia),
            t.stavba,
            lambda a: f"verzia {a.get('verzia') or 'rozhovoru'}",
        ),
        Tool(
            "plan_uloh",
            "Plán úloh verzie (EPIC → FEAT → úloha) so stavom každej úlohy.",
            _schema(verzia),
            t.plan_uloh,
            lambda a: f"verzia {a.get('verzia') or 'rozhovoru'}",
        ),
        Tool(
            "git_historia",
            "História zmien projektu (git log): číslo commitu, dátum, autor, popis. Voliteľne len pre jednu cestu.",
            _schema(
                {
                    "cesta": {"type": "string", "description": "Súbor alebo priečinok v projekte."},
                    "pocet": {"type": "integer", "description": f"Koľko commitov (najviac {_GIT_LOG_MAX})."},
                }
            ),
            t.git_historia,
            lambda a: str(a.get("cesta") or "celý projekt"),
        ),
        Tool(
            "git_zmena",
            "Jedna zmena (git show): popis, zoznam súborov a rozdiely. Commit sa zadáva číslom z git_historia.",
            _schema({"commit": {"type": "string"}}, ["commit"]),
            t.git_zmena,
            lambda a: str(a.get("commit") or ""),
        ),
        Tool(
            "zaznam_agenta",
            "Čo robil agent stavby: posledné kroky z jeho záznamu — nástroj, súbor alebo príkaz, a chyby. "
            "Obsah súborov ani výstupy príkazov tu nie sú.",
            _schema({"pocet": {"type": "integer", "description": f"Koľko krokov (najviac {_AGENT_STEPS_MAX})."}}),
            t.zaznam_agenta,
            lambda a: f"posledných {a.get('pocet') or 80} krokov",
        ),
        Tool(
            "kontajnery",
            "Kontajnery projektu: náhľad Vizuálu, databáza a ťahy stavby, inštalácie UAT jeho zákazníkov — "
            "meno, stav, obraz. PROD tu nie je.",
            _schema(),
            t.kontajnery,
            lambda a: "projekt",
        ),
        Tool(
            "logy",
            "Koniec logu jedného kontajnera zo zoznamu, ktorý vráti nástroj kontajnery.",
            _schema(
                {
                    "kontajner": {"type": "string"},
                    "riadkov": {
                        "type": "integer",
                        "description": f"Koľko riadkov od konca (najviac {_LOG_LINES_MAX}).",
                    },
                },
                ["kontajner"],
            ),
            t.logy,
            lambda a: f"{a.get('kontajner', '')}, {a.get('riadkov') or 200} riadkov",
        ),
        Tool(
            "ci",
            "Výsledok zostavenia (CI) pre posledný commit projektu; keď zlyhalo, aj koniec logu zlyhaného kroku.",
            _schema(),
            t.ci,
            lambda a: "posledný commit",
        ),
        Tool(
            "znalostna_baza",
            "Vyhľadávanie v Znalostnej báze ICC s právami človeka, ktorý sa pýta. Vráti cesty dokumentov a úryvky.",
            _schema({"dotaz": {"type": "string"}}, ["dotaz"]),
            t.znalostna_baza,
            lambda a: f"„{str(a.get('dotaz', ''))[:80]}“",
        ),
        Tool(
            "znalostna_baza_dokument",
            "Celý dokument zo Znalostnej bázy podľa cesty z vyhľadávania (s právami človeka, ktorý sa pýta).",
            _schema({"cesta": {"type": "string"}}, ["cesta"]),
            t.znalostna_baza_dokument,
            lambda a: str(a.get("cesta", "")),
        ),
        Tool(
            "databaza_uat",
            "Jeden dotaz SELECT, WITH alebo EXPLAIN do databázy inštalácie UAT — len na čítanie, najviac "
            f"{_DB_ROWS} riadkov a {_DB_TIMEOUT_S} s. Stĺpce s heslami a kľúčmi databáza nevydá. "
            "Pri viacerých zákazníkoch zadaj zakaznik.",
            _schema({"dotaz": {"type": "string"}, "zakaznik": {"type": "string"}}, ["dotaz"]),
            t.databaza_uat,
            lambda a: f"UAT {a.get('zakaznik') or ''}".strip(),
        ),
    ]
