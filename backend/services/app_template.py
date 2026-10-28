"""DEV-253 — a new project on an app template: the frame, the sign-in, Nastavenia and the server from nex-app-kit.

The Manažér picks the template when founding the project (Director 28.10.2026: „pri každom novom projekte by som mal
možnosť vybrať vytvoriť vizuál novej aplikácie podľa nejakého šablónu“). After init.sh and the agents' charters, the
founding fetches the newest release of nex-app-kit, fills the template's scaffold with the project's values
(``scaffold/render.py`` of that release — the same tool the kit's CI fills it with) over the folder init.sh made,
writes the frontend's lockfile and commits it all, so the first push already carries the template.

The template is the kit's, not the cockpit's: a better frame is a new release of nex-app-kit, a new template a new
folder in it. The cockpit only knows which templates exist (:data:`TEMPLATES`) and what to give the renderer."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Template:
    #: The name the Manažér sees in the new-project form.
    label: str
    #: One sentence under the name: what the project gets from the template.
    description: str


#: The templates a project can be founded on, by the ids of nex-app-kit's scaffold folders.
TEMPLATES: dict[str, Template] = {
    "nsv-standard": Template(
        label="NSV štandard",
        description="Ľavý panel, prihlásenie, Nastavenia a server ako v NSV — agent stavia len obrazovky aplikácie.",
    ),
}
#: What the new-project form offers first.
DEFAULT_TEMPLATE = "nsv-standard"

KIT_REPO = "rauschiccsk/nex-app-kit"
KIT_GIT_URL = f"https://github.com/{KIT_REPO}.git"
TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$")
#: How long each outside step may take — the clone and the lockfile go over the network.
STEP_TIMEOUT = 600

Runner = Callable[..., subprocess.CompletedProcess]


class AppTemplateError(RuntimeError):
    """Founding on the template failed — the message says what, in a sentence for the Manažér."""


@dataclass(frozen=True)
class TemplateChoice:
    template: str
    #: An agent role „assistant“ in the manifest to start with — the app renames or adds roles later.
    ai: bool


def template_of_slug(slug: str) -> str | None:
    """The app template the project was founded on (``projects.template``), or ``None`` — for the steps that know a
    project only by its slug (a build turn's rules, the Vizuál brief, the boot check)."""
    from sqlalchemy import select

    from backend.db.models.projects import Project
    from backend.db.session import SessionLocal

    with SessionLocal() as db:
        return db.execute(select(Project.template).where(Project.slug == slug)).scalar_one_or_none()


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False, **kwargs)


def latest_kit_version(run: Runner = _run) -> str:
    """The newest release of nex-app-kit (its highest vX.Y.Z tag)."""
    result = run(["git", "ls-remote", "--tags", KIT_GIT_URL])
    if result.returncode != 0:
        raise AppTemplateError(
            "Šablónu aplikácie sa nepodarilo načítať — GitHub neodpovedal na zoznam verzií nex-app-kit."
        )
    versions = [tuple(int(x) for x in m.groups()) for line in result.stdout.splitlines() if (m := TAG.search(line))]
    if not versions:
        raise AppTemplateError("Šablóna aplikácie nex-app-kit nemá žiadnu vydanú verziu.")
    return ".".join(str(x) for x in max(versions))


def initials_of(name: str) -> str:
    """The app's initials for the brand square: the first letters of up to four words („NEX Knowledge“ → NK)."""
    words = [w for w in re.split(r"[\s\-_]+", name.strip()) if w and w[0].isalnum()]
    letters = "".join(w[0] for w in words[:4]).upper()
    if len(letters) == 1:
        letters = "".join(ch for ch in words[0] if ch.isalnum())[:2].upper()
    return letters or "AP"


def scaffold_values(
    *, name: str, slug: str, auth_mode: str, ai: bool, ports: tuple[int, int, int], version: str
) -> dict:
    backend_port, frontend_port, db_port = ports
    return {
        "app_name": name,
        "app_initials": initials_of(name),
        "slug": slug,
        # The cockpit says token for an app launched from NEX Manager; the template says manager.
        "auth": "manager" if auth_mode == "token" else "password",
        "ai": ai,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "db_port": db_port,
        "kit_frontend": f"github:{KIT_REPO}#v{version}",
        "kit_server": f"nex-app-kit @ https://github.com/{KIT_REPO}/archive/refs/tags/v{version}.tar.gz#subdirectory=server",
    }


def _fetch_kit(version: str, destination: Path, run: Runner) -> None:
    result = run(["git", "clone", "--depth", "1", "--branch", f"v{version}", KIT_GIT_URL, str(destination)])
    if result.returncode != 0:
        raise AppTemplateError(f"Šablónu aplikácie (nex-app-kit v{version}) sa nepodarilo stiahnuť z GitHubu.")


def apply_template(
    target: Path,
    choice: TemplateChoice,
    *,
    name: str,
    slug: str,
    auth_mode: str,
    ports: tuple[int, int, int],
    run: Runner = _run,
    fetch_kit: Callable[[str, Path, Runner], None] = _fetch_kit,
) -> str | None:
    """Fill the template over the project init.sh founded and commit it. Returns the kit's version, or ``None`` when
    there is no checkout on disk (dry-run / disabled bootstrap — like the charters, nothing to fill)."""
    if choice.template not in TEMPLATES:
        raise AppTemplateError(f"Šablóna „{choice.template}“ neexistuje.")
    if not (target / ".git").is_dir():
        logger.info("App template skipped for %s — no checkout at %s", slug, target)
        return None
    version = latest_kit_version(run)
    with tempfile.TemporaryDirectory(prefix="nex-app-kit-") as tmp:
        kit = Path(tmp) / "kit"
        fetch_kit(version, kit, run)
        values = Path(tmp) / "values.json"
        values.write_text(
            json.dumps(
                scaffold_values(name=name, slug=slug, auth_mode=auth_mode, ai=choice.ai, ports=ports, version=version)
            ),
            encoding="utf-8",
        )
        filled = run(
            [
                "python3",
                str(kit / "scaffold" / "render.py"),
                choice.template,
                "--values",
                str(values),
                "--into",
                str(target),
            ]
        )
        if filled.returncode != 0:
            raise AppTemplateError(
                f"Kostru šablóny sa nepodarilo vyplniť: {filled.stderr.strip() or filled.stdout.strip()}"
            )
    lock = run(["npm", "install", "--package-lock-only", "--no-audit", "--no-fund"], cwd=str(target / "frontend"))
    if lock.returncode != 0:
        raise AppTemplateError("Frontendu šablóny sa nepodarilo pripraviť zoznam balíkov (npm install).")
    paths = ["frontend", "backend", "docker-compose.yml", "docs/AKO_ZACAT.md", ".env.example"]
    added = run(["git", "add", "--all", "--", *paths], cwd=str(target))
    committed = run(
        [
            "git",
            "commit",
            "-m",
            f"feat(scaffold): the frame of the template {choice.template} (nex-app-kit v{version})",
        ],
        cwd=str(target),
    )
    if added.returncode != 0 or committed.returncode != 0:
        raise AppTemplateError("Kostru šablóny sa nepodarilo zapísať do gitu projektu.")
    logger.info("App template %s v%s filled for %s", choice.template, version, slug)
    return version
