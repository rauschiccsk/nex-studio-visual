"""The Projektant's tools in a new idea (Nápady, DEV-318, ``docs/specs/ideas.md`` §4.3).

An idea belongs to no project, so none of the project's tools fit it. The Projektant reads here what helps shape a new
project: the Knowledge Base (by the rights of the person asking), every project of the cockpit, their documentation
(not code, not data), what the cockpit can found and a project's Zásobník — read from the sources the screens read.

DEV-345: every project, whoever it belongs to — until 10.11.2026 only the projects of the person asking. Director: „Pri
návrhu často potrebujem odkázať sa na riešenie, ktoré sme robili v inom projekte, a kvôli tomu, aby projektant to
pozrel nechcem zbytočne presúvať projekty medzi manažérmi.“ For everyone in Nápady: „Áno“. Reading only; the screens
and the project's Poradca keep the owner rule (``authz``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from backend.core import authz
from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services import secret_files
from backend.services.poradca.mcp_server import Tool, ToolError
from backend.services.poradca.tools import PoradcaTools, _schema, knowledge_tools

#: The most projects one overview lists — far more than the cockpit has; a longer list is cut with a sentence.
_PROJECTS_MAX = 200
#: The most documents one listing names, and the most characters of one document.
_DOCS_MAX = 300
_DOC_CHARS = 60_000
#: What of a project is its documentation — never its code.
_DOCS_DIR = "docs"
_ROOT_DOCS = ("README.md",)


class IdeaTools:
    """Tools of one question in a new idea — bound to the person asking, never to a project."""

    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id

    def _user(self, db) -> User:
        user = db.get(User, self.user_id)
        if user is None:
            raise ToolError("Človek, ktorý sa pýta, už v kokpite nie je.")
        return user

    def _project(self, db, slug: object) -> Project:
        """The project by its slug — any project of the cockpit, whoever it belongs to (DEV-345)."""
        if not isinstance(slug, str) or not slug.strip():
            raise ToolError("Zadaj slug projektu, ako ho vrátil nástroj projekty.")
        project = db.execute(select(Project).where(Project.slug == slug.strip())).scalar_one_or_none()
        if project is None:
            raise ToolError(f"Projekt „{slug}“ v kokpite nie je.")
        self._user(db)  # the person asking is still in the cockpit
        return project

    async def projekty(self, args: dict) -> str:
        """Every project of the cockpit, with what a new idea is compared with and who manages it (DEV-345)."""
        from backend.services import project as project_service

        with SessionLocal() as db:
            self._user(db)
            rows = project_service.list_projects(db, created_by=None, limit=_PROJECTS_MAX, offset=0)
            if not rows:
                return "V kokpite nie je žiadny projekt."
            lines = [f"Projekty kokpitu ({len(rows)}):"]
            for p in rows:
                customers = db.execute(
                    select(Customer.name).where(Customer.project_id == p.id).order_by(Customer.name)
                ).scalars()
                versions = db.execute(
                    select(Version.version_number).where(Version.project_id == p.id).order_by(Version.created_at)
                ).scalars()
                manager = db.get(User, authz.project_owner_id(p))
                lines.append(
                    f"- {p.name} ({p.slug}) — manažér: {_person(manager)}; typ {p.type}, prihlásenie {p.auth_mode}, "
                    f"šablóna {p.template or 'žiadna'}, stav {p.status}; "
                    f"zákazníci: {', '.join(customers) or 'žiadny'}; verzie: {', '.join(versions) or 'žiadna'}; "
                    f"opis: {' '.join((p.description or '').split()) or '—'}"
                )
            if len(rows) == _PROJECTS_MAX:
                lines.append(f"(zoznam má najviac {_PROJECTS_MAX} projektov)")
            return "\n".join(lines)

    async def dokumentacia_projektu(self, args: dict) -> str:
        """A project's documentation — the list of its documents, or one of them; never its code."""
        from backend.services.claude_agent import PROJECTS_ROOT

        with SessionLocal() as db:
            project = self._project(db, args.get("projekt"))
            root = os.path.realpath(PROJECTS_ROOT / project.slug)
        wanted = args.get("cesta")
        if not wanted:
            return _list_docs(root, project.slug)
        return _read_doc(root, str(wanted))

    async def moznosti_zalozenia(self, args: dict) -> str:
        """What the cockpit can found — the very options the form Nový projekt reads (``/projects/create-presets``)."""
        from backend.api.routes.projects import get_create_presets

        with SessionLocal() as db:
            presets = get_create_presets(db)
        templates = "; ".join(f"{t.id} — {t.label}" for t in presets.templates) or "žiadna"
        web = "áno" if presets.web_available else f"nie — {presets.web_note}"
        return "\n".join(
            [
                "Čo kokpit pri založení projektu vie (tie isté možnosti ako formulár Nový projekt):",
                "- typ: standard (jeden frontend); web (administrácia a verejný web na jednom backende) — "
                f"dostupný: {web}",
                "- prihlásenie: password (meno a heslo); token (aplikáciu spúšťa NEX Manager)",
                f"- šablóny aplikácií: {templates}; predvolená: {presets.default_template or 'žiadna'} — šablóna "
                "len pre typ standard",
                "- porty pridelí kokpit z evidencie stroja; zákazníka zapíše človek po založení na obrazovke Zákazníci",
            ]
        )

    async def zasobnik_projektu(self, args: dict) -> str:
        """A project's Zásobník — read by the same tool Poradca has inside the project."""
        with SessionLocal() as db:
            project = self._project(db, args.get("projekt"))
            project_id = project.id
        return await PoradcaTools(project_id=project_id, version_id=None, user_id=self.user_id).zasobnik({})


def _person(user: Optional[User]) -> str:
    if user is None:
        return "—"
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.username


def _list_docs(root: str, slug: str) -> str:
    found: list[str] = [name for name in _ROOT_DOCS if os.path.isfile(os.path.join(root, name))]
    base = os.path.join(root, _DOCS_DIR)
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".md") and not secret_files.SECRET_FILE_RE.match(name):
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
    if not found:
        return f"Projekt {slug} nemá dokumentáciu (docs/ ani README.md)."
    lines = [f"Dokumentácia projektu {slug} ({len(found)} dokumentov; prečítaš ich s cesta=…):"]
    lines += [f"- {path}" for path in found[:_DOCS_MAX]]
    if len(found) > _DOCS_MAX:
        lines.append(f"(a ďalších {len(found) - _DOCS_MAX})")
    return "\n".join(lines)


def _read_doc(root: str, wanted: str) -> str:
    full = os.path.realpath(os.path.join(root, wanted.lstrip("/")))
    rel = os.path.relpath(full, root)
    inside_docs = full.startswith(os.path.join(root, _DOCS_DIR) + os.sep)
    if not (inside_docs or rel in _ROOT_DOCS) or not rel.endswith(".md"):
        raise ToolError("Čítam len dokumentáciu projektu — súbory .md v docs/ a README.md, nie kód.")
    if secret_files.SECRET_FILE_RE.match(os.path.basename(full)) or not os.path.isfile(full):
        raise ToolError(f"Dokument {rel} v projekte nie je.")
    text: Optional[str] = None
    try:
        text = Path(full).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"Dokument {rel} sa nedá prečítať.") from exc
    if len(text) > _DOC_CHARS:
        text = text[:_DOC_CHARS] + "\n… (skrátené)"
    return text


def build_idea_tools(*, user_id: UUID) -> list[Tool]:
    """Tools of one question in a new idea. The agent reads the descriptions — they say what each returns."""
    t = IdeaTools(user_id=user_id)
    projekt = {"projekt": {"type": "string", "description": "Slug projektu, ako ho vrátil nástroj projekty."}}
    return [
        *knowledge_tools(PoradcaTools(project_id=None, version_id=None, user_id=user_id)),
        Tool(
            "projekty",
            "Všetky projekty kokpitu, bez ohľadu na to, komu patria: meno, slug, manažér, typ, prihlásenie, šablóna, "
            "stav, zákazníci, verzie a opis — aby si nový nápad porovnal s tým, čo už máme.",
            _schema(),
            t.projekty,
            lambda a: "projekty kokpitu",
        ),
        Tool(
            "dokumentacia_projektu",
            "Dokumentácia projektu (súbory .md v docs/ a README.md, nie kód): bez cesty zoznam dokumentov, s cestou "
            "jeden dokument.",
            _schema(
                {**projekt, "cesta": {"type": "string", "description": "Cesta dokumentu zo zoznamu."}}, ["projekt"]
            ),
            t.dokumentacia_projektu,
            lambda a: f"{a.get('projekt', '')}/{a.get('cesta') or 'zoznam'}",
        ),
        Tool(
            "moznosti_zalozenia",
            "Čo kokpit pri založení projektu vie: typy, prihlásenie a šablóny aplikácií — tie isté možnosti ako "
            "formulár Nový projekt.",
            _schema(),
            t.moznosti_zalozenia,
            lambda a: "formulár Nový projekt",
        ),
        Tool(
            "zasobnik_projektu",
            "Zásobník projektu: požiadavky REQ-číslo so stavom, prioritou, názvom a začiatkom popisu — pred "
            "doplnením projektu over, či tam podobná požiadavka už nie je.",
            _schema(projekt, ["projekt"]),
            t.zasobnik_projektu,
            lambda a: str(a.get("projekt", "")),
        ),
    ]
