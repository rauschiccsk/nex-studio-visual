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

from backend.services import nexshared

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

KIT_PACKAGE = "nex-app-kit"
KIT_REPO = f"rauschiccsk/{KIT_PACKAGE}"
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
        "kit_frontend": nexshared.git_dependency(KIT_PACKAGE, version),
        "kit_server": kit_server_dependency(version),
    }


def kit_server_dependency(version: str) -> str:
    """The server's dependency on a nex-app-kit release (``backend/pyproject.toml``): the archive's ``server``."""
    return f"{KIT_PACKAGE} @ https://github.com/{KIT_REPO}/archive/refs/tags/v{version}.tar.gz#subdirectory=server"


def _fetch_kit(version: str, destination: Path, run: Runner) -> None:
    result = run(["git", "clone", "--depth", "1", "--branch", f"v{version}", KIT_GIT_URL, str(destination)])
    if result.returncode != 0:
        raise AppTemplateError(f"Šablónu aplikácie (nex-app-kit v{version}) sa nepodarilo stiahnuť z GitHubu.")


def _render(
    template: str,
    values: dict,
    mode: str,
    destination: Path,
    tmp: Path,
    run: Runner,
    fetch_kit: Callable[[str, Path, Runner], None],
) -> None:
    """Fill *template*'s scaffold of the kit release in *values* — ``--into`` a founded project, ``--out`` a fresh
    folder — with the release's own renderer (the one its CI fills the scaffold with)."""
    version = values["kit_frontend"].rsplit("#v", 1)[1]
    kit = tmp / "kit"
    fetch_kit(version, kit, run)
    values_file = tmp / "values.json"
    values_file.write_text(json.dumps(values), encoding="utf-8")
    filled = run(
        ["python3", str(kit / "scaffold" / "render.py"), template, "--values", str(values_file), mode, str(destination)]
    )
    if filled.returncode != 0:
        raise AppTemplateError(
            f"Kostru šablóny sa nepodarilo vyplniť: {filled.stderr.strip() or filled.stdout.strip()}"
        )


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
    values = scaffold_values(name=name, slug=slug, auth_mode=auth_mode, ai=choice.ai, ports=ports, version=version)
    with tempfile.TemporaryDirectory(prefix="nex-app-kit-") as tmp:
        _render(choice.template, values, "--into", target, Path(tmp), run, fetch_kit)
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


# ── DEV-254: an existing project onto the template ───────────────────────────────────────────────────────────────
# Director 29.10.2026: „Začneme s projektom nex-knowledge“ — a project built before the template keeps its screens,
# routes and tables. The cockpit adds what makes it a project on the template (the manifest, nex-app-kit on both
# sides, nex-shared at least as new as the release was proven with) in one commit; the next version moves the screens
# into the frame and removes the project's own frame (the agents read the template's rules from then on, and the boot
# check refuses a frame that is not whole). Nothing of the project is deleted here.

MANIFEST = "frontend/app.manifest.json"
_CONVERTED_PATHS = (MANIFEST, "frontend/package.json", "frontend/package-lock.json", "backend/pyproject.toml")
#: The first ``dependencies = [`` of the ``[project]`` table, written one item per line.
_PROJECT_DEPENDENCIES_RE = re.compile(
    r"^\[project\][ \t]*\n(?:(?!\[).*\n)*?dependencies[ \t]*=[ \t]*\[[ \t]*\n", re.MULTILINE
)
_PIN_VERSION_RE = re.compile(r"#v(\d+\.\d+\.\d+)$")


def conversion_refusal(db, project) -> str | None:
    """Why *project* cannot be put onto an app template now, in a sentence for the Manažér — ``None`` when it can.
    The form offers the conversion greyed out with this sentence; the route refuses it with the same one."""
    from sqlalchemy import select

    from backend.db.models.deploy import DeployEvent
    from backend.db.models.versions import Version

    if project.template:
        return "Projekt už je na šablóne aplikácie."
    if project.type != "standard":
        return "Na šablónu sa zatiaľ prevádza len bežný projekt — Web projekt nie."
    if not project.source_path:
        return "Projekt nemá zdrojový priečinok, prevod nemá kam zapísať."
    active = (
        db.execute(select(Version.version_number).where(Version.project_id == project.id, Version.status == "active"))
        .scalars()
        .first()
    )
    if active:
        return f"Projekt má rozpracovanú verziu {active} — prevod počká, kým ju dokončíš."
    deployed = db.execute(
        select(DeployEvent.id).where(
            DeployEvent.project_id == project.id, DeployEvent.event_type == "deploy", DeployEvent.status == "ok"
        )
    ).first()
    if deployed:
        return (
            "Projekt už má nasadenú inštaláciu — pri prevode treba jeho používateľov, relácie a nastavenia preniesť "
            "do tabuliek šablóny a to kokpit zatiaľ nevie."
        )
    return None


def with_server_dependency(pyproject_text: str, dependency: str) -> str | None:
    """``backend/pyproject.toml`` with *dependency* first in ``[project].dependencies``, or ``None`` when the file has
    no such list written one item per line (then the cockpit does not guess where it goes)."""
    match = _PROJECT_DEPENDENCIES_RE.search(pyproject_text)
    if match is None:
        return None
    return pyproject_text[: match.end()] + f'    "{dependency}",\n' + pyproject_text[match.end() :]


def _pinned(spec: str) -> str | None:
    match = _PIN_VERSION_RE.search(spec or "")
    return match.group(1) if match else None


def with_template_packages(project_package: dict, scaffold_package: dict) -> tuple[dict, list[str]]:
    """The project's ``package.json`` with what the template's frontend needs: nex-app-kit at the release, nex-shared at
    least at the release's own pin, every other package of the template the project lacks (in the same section). The
    project's own packages and versions stay. Returns the package and the git specs npm must re-resolve — a git
    dependency already in the lockfile is not re-resolved by a bare install (:func:`nexshared.resolve_lockfile`)."""
    package = json.loads(json.dumps(project_package))
    present = {**package.get("devDependencies", {}), **package.get("dependencies", {})}
    resolve: list[str] = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (scaffold_package.get(section) or {}).items():
            if name == KIT_PACKAGE:
                package.setdefault(section, {})[name] = spec
                resolve.append(spec)
            elif name == nexshared.PACKAGE:
                own, wanted = _pinned(present.get(name, "")), _pinned(spec)
                if own is None or (wanted and nexshared._key(own) < nexshared._key(wanted)):
                    where = "dependencies" if name in package.get("dependencies", {}) else section
                    package.setdefault(where, {})[name] = spec
                    resolve.append(spec)
            elif name not in present:
                package.setdefault(section, {})[name] = spec
    return package, resolve


def convert_project(
    target: Path,
    choice: TemplateChoice,
    *,
    name: str,
    slug: str,
    auth_mode: str,
    ports: tuple[int, int, int],
    run: Runner = _run,
    fetch_kit: Callable[[str, Path, Runner], None] = _fetch_kit,
) -> str:
    """Put an existing project onto the app template — its manifest, nex-app-kit in the frontend (pin and lockfile)
    and the server, nex-shared raised to the release's pin when older — and commit it. Returns the kit's version.
    Every file it touched is put back when any step fails; the project's own code is never touched."""
    if choice.template not in TEMPLATES:
        raise AppTemplateError(f"Šablóna „{choice.template}“ neexistuje.")
    if not (target / ".git").is_dir():
        raise AppTemplateError("Projekt nemá na disku git — prevod na šablónu nemá kam zapísať.")
    if (target / MANIFEST).exists():
        raise AppTemplateError(f"Projekt už má manifest šablóny ({MANIFEST}) — prevod sa druhýkrát nespúšťa.")
    files = {rel: target / rel for rel in _CONVERTED_PATHS}
    for rel in ("frontend/package.json", "backend/pyproject.toml"):
        if not files[rel].is_file():
            raise AppTemplateError(f"Projektu chýba {rel} — prevod na šablónu ho potrebuje.")
    pyproject_text = files["backend/pyproject.toml"].read_text(encoding="utf-8")
    if with_server_dependency(pyproject_text, KIT_PACKAGE) is None:
        raise AppTemplateError(
            "Server projektu nemá zoznam závislostí v [project].dependencies (po jednej na riadok) — prevod na šablónu "
            "doň nevie doplniť nex-app-kit."
        )
    version = latest_kit_version(run)
    values = scaffold_values(name=name, slug=slug, auth_mode=auth_mode, ai=choice.ai, ports=ports, version=version)
    pyproject = with_server_dependency(pyproject_text, values["kit_server"])
    originals = {path: path.read_text(encoding="utf-8") if path.is_file() else None for path in files.values()}
    try:
        with tempfile.TemporaryDirectory(prefix="nex-app-kit-") as tmp:
            filled = Path(tmp) / "filled"
            _render(choice.template, values, "--out", filled, Path(tmp), run, fetch_kit)
            files[MANIFEST].write_text((filled / MANIFEST).read_text(encoding="utf-8"), encoding="utf-8")
            package, specs = with_template_packages(
                json.loads(originals[files["frontend/package.json"]]),
                json.loads((filled / "frontend" / "package.json").read_text(encoding="utf-8")),
            )
        files["frontend/package.json"].write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", "utf-8")
        files["backend/pyproject.toml"].write_text(pyproject, encoding="utf-8")
        lock = run(
            ["npm", "install", "--package-lock-only", "--no-audit", "--no-fund", *specs], cwd=str(target / "frontend")
        )
        if lock.returncode != 0 or nexshared.effective_version(str(target), "frontend", KIT_PACKAGE) != version:
            raise AppTemplateError("Frontendu sa nepodarilo pripraviť zoznam balíkov so šablónou (npm install).")
        added = run(["git", "add", "--", *_CONVERTED_PATHS], cwd=str(target))
        committed = run(
            [
                "git",
                "commit",
                "-m",
                f"feat(template): onto the app template {choice.template} — its manifest and nex-app-kit v{version}",
            ],
            cwd=str(target),
        )
        if added.returncode != 0 or committed.returncode != 0:
            raise AppTemplateError("Prevod na šablónu sa nepodarilo zapísať do gitu projektu.")
    except (AppTemplateError, OSError, ValueError) as exc:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
        if isinstance(exc, AppTemplateError):
            raise
        raise AppTemplateError(f"Prevod na šablónu sa nepodaril: {exc}") from exc
    logger.info("Project %s put onto the app template %s (nex-app-kit v%s)", slug, choice.template, version)
    return version
