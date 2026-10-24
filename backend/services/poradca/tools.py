"""Poradca tools for a view "from behind" (ICCINT-167, design §4.2).

Everything is served by the BACKEND: the container name, directory or database is not chosen by the agent but by the
cockpit from the conversation's project. The agent may pick only from what the cockpit itself offers (e.g. a container
name from the ``kontajnery`` list); the tool rejects anything else with a sentence. The output of each tool passes the
secrets filter and the length cap in :mod:`.mcp_server` — here it is only collected.

PROD is never returned: Poradca does not see customers' live operation (Director's decision of 05.10.2026).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core.local_time import local_time
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services import version_notes
from backend.services.poradca.context import UatInstallation, uat_installations
from backend.services.poradca.mcp_server import Tool, ToolError

#: Names of the Riadiace centrum buttons by the action the cockpit offers (``available_actions``). Poradca
#: advises with the button the human sees on screen — not with a name in code. A test guards them: an action that the
#: frontend knows and that is missing here turns red.
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
    "schvalit_schemu": "Schváliť štruktúru databázy (len Ri)",
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
#: DEV-33: the Zásobník as the screen names it (BacklogPage) — Poradca speaks to the Manažér in his words.
_BACKLOG_LIMIT = 200
_BACKLOG_DESCRIPTION_CHARS = 200
_BACKLOG_STATUS = {
    "open": "Otvorené",
    "included": "Vo verzii",
    "realized": "Realizované vo verzii",
    "rejected": "Zamietnuté",
}
_BACKLOG_PRIORITY = {"low": "nízka", "medium": "stredná", "high": "vysoká", "critical": "kritická"}

#: DEV-181 — the other screens of a project.
_REQUIREMENT_CHARS = 20_000
_VERSIONS_LIMIT = 50
_DEPLOYS_MAX = 50
_TICKETS_LIMIT = 100
_BILLING_STATE = {
    "issued": "vystavené",
    "ready": "dá sa vystaviť",
    "needs_kind": "čaká na rozhodnutie o druhu práce",
    "cannot": "nedá sa vystaviť",
    "blocked": "nedá sa spočítať",
    "not_done": "ešte sa stavia",
}

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_UAT_DB_ROLE = "poradca_ro"


def _schema(properties: Optional[dict] = None, required: Optional[list[str]] = None) -> dict:
    out: dict = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


async def _run(argv: list[str], *, cwd: Optional[str] = None, timeout: int = _CMD_TIMEOUT_S) -> tuple[int, str]:
    """Runs a command WITHOUT a shell (argument list) and returns ``(code, stdout+stderr)``."""
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


def _history_line(row: str) -> str:
    """One commit of ``git_historia`` as the Manažér reads it — its time on the local clock (DEV-59)."""
    parts = row.split("\x1f")
    if len(parts) != 4:
        return row
    sha, iso, author, subject = parts
    try:
        when = local_time(datetime.fromisoformat(iso)).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        when = iso
    return f"{sha}  {when}  {author}  {subject}"


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


def _requirements(n: int) -> str:
    """„1 požiadavka", „3 požiadavky", „5 požiadaviek" — the Slovak plural the screen would say."""
    if n == 1:
        return "1 požiadavka"
    return f"{n} požiadavky" if 2 <= n <= 4 else f"{n} požiadaviek"


class PoradcaTools:
    """Tools of one question — bound to the project, the conversation's version and the person asking."""

    def __init__(self, *, project_id: UUID, version_id: Optional[UUID], user_id: UUID) -> None:
        self.project_id = project_id
        self.version_id = version_id
        self.user_id = user_id

    # ── helpers ────────────────────────────────────────────────────────────────

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
        from backend.services import orchestrator

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
            labels = dict(ACTION_LABELS)
            if "pokracovat" in actions and orchestrator.plan_awaits_start(db, version.id):
                # DEV-214: at the stop after the task plan the same verb STARTS the coding — the screen says so.
                labels["pokracovat"] = "Spustiť programovanie"
            if actions:
                lines.append(
                    "Tlačidlá, ktoré Manažér v Riadiacom centre práve vidí: "
                    + "; ".join(f"„{labels.get(a, a)}“ ({a})" for a in actions)
                    + "."
                )
            else:
                lines.append("Manažér teraz v Riadiacom centre nevidí žiadne tlačidlo stavby.")
            if "decide" in actions:
                lines.extend(_cards_lines(db, version.id))
            if state.current_stage == "vizual":
                lines.extend(_vizual_checklist_lines(board.vizual_checklists))
            # DEV-151: what the people wrote about why the development stands — Poradca reads everything the Manažér
            # reads (DEV-181); the build agent never gets it.
            note = version_notes.latest(db, version.id)
            if note is not None:
                when = local_time(note.created_at).strftime("%d.%m.%Y %H:%M")
                lines.append(
                    f"Kde sme skončili (poznámka ľudí): „{note.text}“ — "
                    f"{note.author_name or 'bývalý používateľ'}, {when}."
                )
            lines.append(f"\nPosledných {len(board.recent_messages or [])} správ rozhovoru stavby (najstaršia prvá):")
            for msg in board.recent_messages or []:
                text = (msg.content or "").strip()
                if len(text) > _MESSAGE_CHARS:
                    text = text[:_MESSAGE_CHARS] + " …"
                when = local_time(msg.created_at).strftime("%d.%m. %H:%M") if msg.created_at else ""
                lines.append(f"— [{when}] {msg.author} → {msg.recipient} ({msg.kind}, fáza {msg.stage}):\n{text}")
                lines.extend(_finding_lines(msg))
            return "\n".join(lines)

    # ── zásobník ───────────────────────────────────────────────────────────────

    async def zasobnik(self, args: dict) -> str:
        """DEV-33: the project's Zásobník, so Poradca names a requirement that is already there (REQ-N) instead of
        proposing it again — 08.10.2026 it offered the credit-note requirement a second time, and the click
        „Uložiť do Zásobníka" would have created a duplicate of REQ-1. Read through the backlog service the
        Zásobník screen uses, never a copy of its query."""
        from backend.services import backlog as backlog_service

        with SessionLocal() as db:
            items = backlog_service.list_backlog(db, project_id=self.project_id, limit=_BACKLOG_LIMIT)
            if not items:
                return "Zásobník projektu je prázdny."
            numbers = {
                v.id: v.version_number
                for v in db.execute(select(Version).where(Version.project_id == self.project_id)).scalars()
            }
            lines = [f"Zásobník projektu — {_requirements(len(items))} (REQ-číslo, stav, názov, popis):"]
            for item in items:
                status = _BACKLOG_STATUS.get(item.status, item.status)
                if item.status in ("included", "realized") and item.version_id in numbers:
                    status += f" {numbers[item.version_id]}"
                line = (
                    f"REQ-{item.number} [{status}, priorita {_BACKLOG_PRIORITY.get(item.priority, item.priority)}] "
                    f"{item.title}"
                )
                description = " ".join((item.description or "").split())
                if description:
                    if len(description) > _BACKLOG_DESCRIPTION_CHARS:
                        description = description[:_BACKLOG_DESCRIPTION_CHARS].rstrip() + " …"
                    line += f" — {description}"
                lines.append(line)
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
            # DEV-59: git gives the time with its zone (ISO 8601); the cockpit puts it on the local clock itself —
            # never the zone of whatever process runs git.
            "--format=%h%x1f%aI%x1f%an%x1f%s",
        ]
        if rel:
            argv += ["--", rel]
        code, out = await _run(argv)
        if code != 0:
            raise ToolError("História zmien sa nedá prečítať.")
        return "\n".join(_history_line(row) for row in out.strip().splitlines()) or "Žiadne zmeny."

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
        # DEV-53: the answer's field is ``stav`` — reading ``state`` raised on every call, so Poradca could not say
        # anything about a build (tests/test_poradca_reads_the_ci_state.py guards every field the tool reads).
        lines = [f"Zostavenie (CI) pre commit {snap.sha or '?'}: {snap.stav} — {snap.detail}"]
        if snap.url:
            lines.append(f"Beh na GitHube: {snap.url}")
        if snap.stav != "red" or not snap.sha:
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

    # ── Knowledge base ──────────────────────────────────────────────────────────

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

    # ── UAT database ─────────────────────────────────────────────────────────────

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

    # ── DEV-181: what the Manažér sees on the project's other screens ─────────

    async def poziadavka(self, args: dict) -> str:
        """One requirement of the Zásobník whole — ``zasobnik`` lists only the start of each."""
        from backend.db.models.backlog import BacklogItem

        cislo = _int_arg(args, "cislo", 0, 1_000_000)
        with SessionLocal() as db:
            item = db.execute(
                select(BacklogItem).where(BacklogItem.project_id == self.project_id, BacklogItem.number == cislo)
            ).scalar_one_or_none()
            if item is None:
                raise ToolError(f"Požiadavka REQ-{cislo} v Zásobníku projektu nie je.")
            status = _BACKLOG_STATUS.get(item.status, item.status)
            if item.version_id is not None:
                version = db.get(Version, item.version_id)
                if version is not None:
                    status += f" {version.version_number}"
            priority = _BACKLOG_PRIORITY.get(item.priority, item.priority)
            lines = [f"REQ-{item.number} [{status}, priorita {priority}] {item.title}"]
            if item.plane_identifier:
                lines.append(f"Tiket v Plane: {item.plane_identifier}")
            if item.rejection_reason:
                lines.append(f"Dôvod zamietnutia: {item.rejection_reason}")
            lines.append("")
            lines.append((item.description or "(bez popisu)")[:_REQUIREMENT_CHARS])
            return "\n".join(lines)

    async def verzie(self, args: dict) -> str:
        """The project's versions as the Verzie screen lists them — the newest first, with where each build stands."""
        from backend.db.models.pipeline import PipelineState

        with SessionLocal() as db:
            rows = db.execute(
                select(Version, PipelineState)
                .outerjoin(PipelineState, PipelineState.version_id == Version.id)
                .where(Version.project_id == self.project_id)
                .order_by(Version.created_at.desc())
                .limit(_VERSIONS_LIMIT)
            ).all()
            if not rows:
                return "Projekt nemá žiadnu verziu."
            lines = [f"Verzie projektu ({len(rows)}, najnovšia prvá):"]
            for version, state in rows:
                line = f"{version.version_number} — {version.status}"
                if state is not None:
                    line += f"; stavba: fáza {state.current_stage}, stav {state.status}"
                    if state.block_reason:
                        line += f", dôvod zastavenia {state.block_reason}"
                if version.release_date:
                    line += f"; vydaná {version.release_date}"
                lines.append(line)
            return "\n".join(lines)

    async def zakaznici(self, args: dict) -> str:
        """The Zákazníci screen: the customers, what the app asks of each (its ``.env.example`` markers) and what each
        has — plain values as the form shows them, a secret only as „uložené“ or „chýba“, never its value."""
        from backend.db.models.customers import Customer
        from backend.services import claude_agent, env_contract, uat_provisioner
        from backend.services import customer as customer_service

        with SessionLocal() as db:
            project = self._project(db)
            customers = (
                db.execute(select(Customer).where(Customer.project_id == project.id).order_by(Customer.name))
                .scalars()
                .all()
            )
            root = claude_agent.PROJECTS_ROOT / project.slug
            contract = env_contract.for_project(root)
            template_keys = list(uat_provisioner._parse_env_file(root / ".env.example"))
            lines = [f"Zákazníci projektu: {len(customers)}."]
            if contract.problems:
                lines.append("Chyby značiek v .env.example: " + "; ".join(contract.problems))
            for c in customers:
                stored = customer_service.secret_setting_keys(db, c)
                lines.append("")
                lines.append(f"{c.name} (skratka {c.slug}, subdoména {c.subdomain or c.slug})")
                if c.notes:
                    lines.append(f"  Poznámka: {c.notes}")
                if c.integrations:
                    lines.append("  Integrácie: zadané (obsah môže niesť prístupové údaje — nevypisuje sa)")
                for s in contract.settings:
                    if s.kind == env_contract.CUSTOMER_SECRET:
                        have = "uložené" if s.key in stored else "chýba"
                    elif s.kind == env_contract.CUSTOMER:
                        have = (c.settings or {}).get(s.key) or "chýba"
                    else:
                        have = "dopĺňa kokpit pri nasadení"
                    must = ", povinné" if s.required else ""
                    lines.append(f"  {s.label} ({s.key}{must}): {have}")
                values = env_contract.customer_values(
                    contract, c.settings or {}, customer_service.read_secret_settings(db, c)
                )
                blockers = env_contract.deploy_blockers(contract, template_keys=template_keys, values=values)
                lines.append(
                    "  Nasadenie sa nedá spustiť: " + " ".join(blockers)
                    if blockers
                    else "  Nastavenia nasadenie nezastavia."
                )
            return "\n".join(lines)

    async def nasadenia(self, args: dict) -> str:
        """The UAT and PROD screens: per customer the deploys and acceptances (status, the sentence, the warnings)
        and the deploys the cockpit refused, with the reason it told the Manažér — the newest first."""
        from backend.db.models.customers import Customer
        from backend.db.models.deploy import DeployEvent, DeployRefusal

        prostredie = str(args.get("prostredie") or "").strip().lower()
        if prostredie not in ("", "uat", "prod"):
            raise ToolError("prostredie je uat alebo prod (bez neho obe).")
        pocet = _int_arg(args, "pocet", 10, _DEPLOYS_MAX)
        with SessionLocal() as db:
            customers = (
                db.execute(select(Customer).where(Customer.project_id == self.project_id).order_by(Customer.name))
                .scalars()
                .all()
            )
            if not customers:
                return "Projekt nemá žiadneho zákazníka — nasadiť ho nie je kam."
            lines: list[str] = []
            for c in customers:
                events = select(DeployEvent).where(DeployEvent.customer_id == c.id)
                refusals = select(DeployRefusal).where(DeployRefusal.customer_id == c.id)
                if prostredie:
                    events = events.where(DeployEvent.environment == prostredie)
                    refusals = refusals.where(DeployRefusal.environment == prostredie)
                found = [
                    (e.created_at, _deploy_line(e))
                    for e in db.execute(events.order_by(DeployEvent.seq.desc()).limit(pocet)).scalars()
                ] + [
                    (r.created_at, _refusal_line(r))
                    for r in db.execute(refusals.order_by(DeployRefusal.created_at.desc()).limit(pocet)).scalars()
                ]
                found.sort(key=lambda pair: pair[0], reverse=True)
                lines.append(f"{c.name} ({c.slug}):")
                lines.extend(f"  {text}" for _when, text in found[:pocet]) if found else lines.append("  nič")
            return "\n".join(lines)

    async def plane_tikety(self, args: dict) -> str:
        """The Plane tikety screen: the tickets of the project's module in Plane, the newest change first."""
        from backend.services import plane_tickets
        from backend.services.plane_gateway import PlaneError

        uzavrete = bool(args.get("uzavrete"))
        with SessionLocal() as db:
            project = self._project(db)
            try:
                found = await asyncio.to_thread(
                    plane_tickets.project_tickets, db, plane_tickets.gateway(), project, closed=uzavrete
                )
            except PlaneError as exc:
                raise ToolError(f"Plane neodpovedal: {exc}") from exc
            if found.module is None:
                return "Projekt nemá v Plane modul — tikety projektu sa v Plane nevedú."
            if not found.tickets:
                return f"V module {found.module} nie sú {'uzavreté' if uzavrete else 'otvorené'} tikety."
            lines = [f"Tikety modulu {found.module} ({'uzavreté' if uzavrete else 'otvorené'}), najnovšia zmena prvá:"]
            for ticket in found.tickets[:_TICKETS_LIMIT]:
                line = f"{ticket.identifier} [{ticket.state}] {ticket.title}"
                if ticket.requirement:
                    line += f" — {ticket.requirement}"
                if ticket.warning:
                    line += f" (pozor: {ticket.warning})"
                lines.append(line)
            return "\n".join(lines)

    async def plane_tiket(self, args: dict) -> str:
        """DEV-218 — one ticket of DEV by its number: what the agent asks for without its content. The same document a
        click then attaches for the agent. Development is kept in DEV only (Director 24.10.2026)."""
        from backend.services import plane_tickets
        from backend.services.plane_gateway import PlaneError

        try:
            ticket = await asyncio.to_thread(
                plane_tickets.read_ticket, plane_tickets.gateway(), str(args.get("cislo") or "")
            )
        except PlaneError as exc:
            raise ToolError(str(exc)) from exc
        return ticket.document()

    async def naklady(self, args: dict) -> str:
        """The Náklady screen: what each version cost and the project in total — in whole euros, as the screen."""
        from backend.services import metrics as metrics_service

        with SessionLocal() as db:
            costs = metrics_service.compute_project_metrics(db, self._project(db))
            lines = ["Náklady projektu (celé eurá, zaokrúhlené nahor ako na obrazovke):"]
            for v in costs.by_version:
                lines.append(f"{v.version_number} ({v.status}): {_euros(v.totals.agent_cost_total)}")
            lines.append(f"Spolu: {_euros(costs.totals.agent_cost_total)}")
            if not costs.totals.agent_cost_complete:
                lines.append("Časť spotreby sa nedala oceniť — čísla sú len ocenená časť.")
            return "\n".join(lines)

    async def fakturacia(self, args: dict) -> str:
        """The Fakturácia screen: every version and where it stands for invoicing. The screen keeps who wrote each
        line it counted; here nothing is committed — the session closes without it."""
        from backend.services import claude_agent
        from backend.services import delivery_statement as statements

        with SessionLocal() as db:
            project = self._project(db)
            rows = await asyncio.to_thread(
                statements.billing_rows, db, project, claude_agent.PROJECTS_ROOT / project.slug
            )
            if not rows:
                return "Projekt nemá žiadnu verziu na fakturáciu."
            lines = ["Fakturácia projektu (najnovšia verzia prvá):"]
            for r in rows:
                line = f"{r.version.version_number}: {_BILLING_STATE.get(r.state, r.state)}"
                if r.valid is not None:
                    line += f", vystavené {r.valid.amount_eur} €"
                elif r.preview.amounts is not None:
                    line += f", na vystavenie {r.preview.amounts.total} €"
                if r.reason:
                    line += f" — {r.reason}"
                lines.append(line)
            return "\n".join(lines)


def _finding_lines(msg: Any) -> list[str]:
    """DEV-181 — the Auditor's notes of a verdict, each whole. The message text only counts them („ostávajú dve
    neblokujúce poznámky“); the notes live in ``payload.findings`` and the Manažér reads them behind the verdict's
    button. 22.10.2026 Poradca could not name the second note of Career Asistent 1.1.0 and sent the Director to look."""
    if getattr(msg, "kind", None) != "verdict":
        return []
    lines = []
    for f in (getattr(msg, "payload", None) or {}).get("findings") or []:
        if not isinstance(f, dict) or not f.get("text"):
            continue
        how = "blokuje" if f.get("blocking") else "neblokuje"
        if f.get("escapes_tests"):
            how += ", skúšky ju nechytia"
        lines.append(f"  poznámka Audítora ({how}): {f['text']}")
    return lines


def _euros(value: Optional[int]) -> str:
    return "nedá sa oceniť" if value is None else f"{value} €"


def _deploy_line(event: Any) -> str:
    """One deploy or acceptance as the UAT / PROD screen shows it — the sentence and every warning."""
    when = local_time(event.created_at).strftime("%d.%m. %H:%M") if event.created_at else ""
    what = "akceptácia" if event.event_type == "accept" else "nasadenie"
    line = f"[{when}] {event.environment.upper()} {what} {event.version_number}: {event.status}"
    if event.detail:
        line += f" — {event.detail[:_MESSAGE_CHARS]}"
    for warning in event.warnings or []:
        line += f"\n    upozornenie: {warning}"
    return line


def _refusal_line(refusal: Any) -> str:
    """A deploy the cockpit refused before it began, with the sentence the Manažér got."""
    when = local_time(refusal.created_at).strftime("%d.%m. %H:%M") if refusal.created_at else ""
    return f"[{when}] {refusal.environment.upper()} nasadenie {refusal.version_number} ODMIETNUTÉ — {refusal.reason}"


def _agent_steps(transcripts: Path, count: int, project_dir: str) -> str:
    """Latest steps of the build agent from Claude Code records — tool, target and error; not file content."""
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
    """Project containers from ``docker ps --format {{json .}}``: Vizuál preview, build, UAT installations."""
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
    """Tools of one question. The agent reads the descriptions — they say what the tool returns and what it does not."""
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
            "zasobnik",
            "Zásobník projektu: požiadavky REQ-číslo so stavom (otvorená, vo verzii, realizovaná, zamietnutá), "
            "prioritou, názvom a začiatkom popisu. Pred návrhom požiadavky do Zásobníka over, či tam podobná "
            "už nie je.",
            _schema(),
            t.zasobnik,
            lambda a: "projekt",
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
        # DEV-181 — „Poradca v projekte musí vidieť všetko“ (Director 22.10.2026): the project's other screens.
        Tool(
            "poziadavka",
            "Jedna požiadavka Zásobníka celá — REQ-číslo, stav, priorita, celý popis, tiket v Plane, dôvod "
            "zamietnutia.",
            _schema({"cislo": {"type": "integer", "description": "Číslo požiadavky, napr. 2 pre REQ-2."}}, ["cislo"]),
            t.poziadavka,
            lambda a: f"REQ-{a.get('cislo', '')}",
        ),
        Tool(
            "verzie",
            "Verzie projektu ako ich ukazuje obrazovka Verzie — číslo, stav a kde stojí stavba každej, najnovšia prvá.",
            _schema(),
            t.verzie,
            lambda a: "projekt",
        ),
        Tool(
            "zakaznici",
            "Obrazovka Zákazníci: zákazníci projektu, čo aplikácia od každého pýta (značky v .env.example), čo má "
            "vyplnené (bežné hodnoty ako formulár, tajomstvo len „uložené“ alebo „chýba“) a čo by zastavilo nasadenie.",
            _schema(),
            t.zakaznici,
            lambda a: "projekt",
        ),
        Tool(
            "nasadenia",
            "Obrazovky UAT a PROD: pri každom zákazníkovi nasadenia a akceptácie (stav, hlásenie, upozornenia) aj "
            "nasadenia, ktoré kokpit odmietol, s vetou, ktorú dostal Manažér — najnovšie prvé.",
            _schema(
                {
                    "prostredie": {"type": "string", "description": "uat alebo prod; bez neho obe."},
                    "pocet": {
                        "type": "integer",
                        "description": f"Koľko záznamov na zákazníka (najviac {_DEPLOYS_MAX}).",
                    },
                }
            ),
            t.nasadenia,
            lambda a: str(a.get("prostredie") or "UAT aj PROD"),
        ),
        Tool(
            "plane_tikety",
            "Obrazovka Plane tikety: tikety modulu projektu v Plane — identifikátor, stav, názov, požiadavka "
            "Zásobníka; predvolene otvorené, s uzavrete=true uzavreté.",
            _schema({"uzavrete": {"type": "boolean", "description": "Uzavreté namiesto otvorených."}}),
            t.plane_tikety,
            lambda a: "uzavreté" if a.get("uzavrete") else "otvorené",
        ),
        Tool(
            "plane_tiket",
            "Jeden tiket evidencie DEV podľa čísla (napr. DEV-186) — názov, stav, celý popis a komentáre. Vývojová "
            "práca sa vedie len v DEV; tiket inej evidencie kokpit nečíta a povie to.",
            _schema({"cislo": {"type": "string", "description": "Číslo tiketu, napr. DEV-186."}}, ["cislo"]),
            t.plane_tiket,
            lambda a: str(a.get("cislo") or ""),
        ),
        Tool(
            "naklady",
            "Obrazovka Náklady: čo stála každá verzia a projekt spolu, v celých eurách ako obrazovka.",
            _schema(),
            t.naklady,
            lambda a: "projekt",
        ),
        Tool(
            "fakturacia",
            "Obrazovka Fakturácia: každá verzia a kde stojí pri fakturácii — vystavené, dá sa vystaviť, prečo nie.",
            _schema(),
            t.fakturacia,
            lambda a: "projekt",
        ),
    ]


#: Files of the project — Poradca reads them with Read, Grep and Glob, not with a cockpit tool.
FILES = "Read/Grep/Glob"

#: DEV-181 — every screen of a project in the cockpit's menu and how Poradca reads what it shows. A screen added to
#: the menu without a line here turns its guard red (``tests/test_poradca_sees_every_screen_of_the_project.py``):
#: Director 22.10.2026 „Poradca v projekte musí vidieť všetko“.
SCREENS: dict[str, tuple[str, ...]] = {
    "Verzie": ("verzie", "stavba"),
    "Zásobník": ("zasobnik", "poziadavka"),
    "Plane tikety": ("plane_tikety", "plane_tiket"),
    "Riadiace centrum": ("stavba", "plan_uloh", "zaznam_agenta"),
    "Dokumenty": (FILES,),
    "Repozitár": ("git_historia", "git_zmena", "ci"),
    "NS Code": (FILES,),
    "Zákazníci": ("zakaznici",),
    "UAT": ("nasadenia", "kontajnery", "logy", "databaza_uat"),
    "PROD": ("nasadenia",),
    "Náklady": ("naklady",),
    "Fakturácia": ("fakturacia",),
}

#: Screens of a project Poradca does not read, and why.
NOT_READ: dict[str, str] = {
    "Poradca": "rozhovor s Poradcom je on sám",
}
