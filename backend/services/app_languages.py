"""DEV-395 — the languages an app on the app template speaks: the project's choice, the installation's default and the
check that every text of its screens is in the catalog of every language.

Director 15.11.2026, Orthodox Hermenia (its screens in Hungarian): „Viacjazyčné aplikácie budeme potrebovať … v NEX
Genesis máme urobené tak, že každý užívateľ môže nastaviť pre seba jazyk aplikácie.“ To the direction (each person
chooses, the texts live in catalogs, the cockpit guards the translations): „Súhlasím“.

What is where:

* the project says which languages its app speaks, the main one first (``projects.languages``) — the main language is
  the one the app's texts are written in first and the one a person sees before choosing;
* a customer may say which of them its installation starts in (``customers.language``) — the cockpit writes it into the
  installation as ``NEX_LANGUAGE``, the template reads it (nex-app-kit ``installation.py``) and each person may still
  choose their own;
* the template knows the languages from nex-app-kit :data:`MIN_KIT_VERSION`; a project on an older release is moved to
  a newer one first (the cockpit's own upgrade, :mod:`backend.services.app_kit`);
* the build agents read the rule in their charters (``templates/app-languages-charter.md``) and Verifikácia measures it
  (:func:`problems`): the manifest names the project's languages, every language has its catalog, no catalog misses a
  text of the main one, and no text a person reads is written into the code — the last by the checker of the very kit
  release the project builds with (``bin/check-texts.mjs``, the rule of the app's own ``src/test/texts.test.ts``).

A project that speaks only Slovak is exactly what it was before: no line in its charters, no key in its scaffold, no
variable in its installation.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

from backend.config.settings import settings

logger = logging.getLogger(__name__)

#: The languages the app template speaks — nex-app-kit's ``KNOWN_LANGUAGES`` (scaffold/render.py), nex-shared's
#: ``LANGUAGES`` — with the name a person reads in the cockpit. A new language is a new catalog in both first.
LANGUAGES: dict[str, str] = {"sk": "slovenčina", "hu": "maďarčina"}
#: What a project speaks when nobody chose otherwise — every project founded before DEV-395.
DEFAULT_LANGUAGES: tuple[str, ...] = ("sk",)
#: The first release of nex-app-kit that speaks more than Slovak (0.14.0 lacked the server's catalogs in its package).
MIN_KIT_VERSION = "0.14.1"
#: The variable the installation's default language is written under (nex-app-kit ``installation.LANGUAGE_KEY``).
INSTALLATION_LANGUAGE_KEY = "NEX_LANGUAGE"

MANIFEST = "frontend/app.manifest.json"
CATALOG_DIR = "frontend/src/locales"
TEXTS_TEST = "frontend/src/test/texts.test.ts"
#: What the check calls itself in the sentences the Manažér reads with the verdict.
CHECK_NAME = "Kontrola jazykov aplikácie"
#: The kit's checker of texts written into the code, at the release the project builds with.
CHECKER_URL = "https://raw.githubusercontent.com/rauschiccsk/nex-app-kit/v{version}/bin/check-texts.mjs"
#: The cockpit's own runner beside its pinned TypeScript parser (templates/dead-code, installed in the image).
_TOOLS_DIR = Path(__file__).resolve().parents[2] / "templates" / "dead-code"
RUNNER = _TOOLS_DIR / "app-texts.mjs"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class CheckNotRun(RuntimeError):
    """A part of the check could not run — the sentence says why; it never stops a version by itself."""


def name_of(code: str) -> str:
    return LANGUAGES.get(code, code)


def describe(languages: Sequence[str]) -> str:
    """„maďarčina (hlavný jazyk), slovenčina“ — the project's languages as a person reads them."""
    names = [name_of(c) for c in languages]
    if len(names) == 1:
        return names[0]
    return f"{names[0]} (hlavný jazyk), " + ", ".join(names[1:])


def is_multilingual(languages: Optional[Sequence[str]]) -> bool:
    """Whether the project speaks anything but Slovak alone — only then does anything of DEV-395 apply to it."""
    return tuple(languages or DEFAULT_LANGUAGES) != DEFAULT_LANGUAGES


def refusal(languages: Sequence[str]) -> Optional[str]:
    """Why *languages* cannot be a project's choice, in a sentence for the Manažér, or ``None``."""
    if not languages:
        return "Aplikácia musí hovoriť aspoň jedným jazykom — vyber hlavný jazyk."
    unknown = [c for c in languages if c not in LANGUAGES]
    if unknown:
        known = ", ".join(f"{name} ({code})" for code, name in LANGUAGES.items())
        return f"Jazyk „{', '.join(unknown)}“ šablóna aplikácie nepozná — pozná tieto: {known}."
    if len(set(languages)) != len(languages):
        return "Každý jazyk aplikácie môže byť vybraný len raz."
    return None


def needs_template_refusal(languages: Sequence[str], template: Optional[str]) -> Optional[str]:
    if is_multilingual(languages) and not template:
        return (
            "Inými jazykmi než slovenčinou hovorí len aplikácia na šablóne aplikácie (NSV štandard) — tá má texty "
            "v katalógoch jazykov."
        )
    return None


def _key(version: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in version.split("."))
    return (a, b, c)


def kit_speaks_languages(version: Optional[str]) -> bool:
    return bool(version) and bool(_SEMVER.match(version or "")) and _key(version) >= _key(MIN_KIT_VERSION)


def kit_version(source_path: str) -> Optional[str]:
    """The release of nex-app-kit the project builds with — the older of its frame (lockfile) and its server (pin), as
    the upgrade prompt reads it; ``None`` when either cannot be read."""
    from backend.services import app_kit, app_template, nexshared

    versions = [
        nexshared.effective_version(source_path, app_kit.FRONTEND, app_template.KIT_PACKAGE),
        app_kit.server_version(source_path),
    ]
    if None in versions:
        return None
    return min(versions, key=_key)


def old_kit_refusal(languages: Sequence[str], source_path: Optional[str]) -> Optional[str]:
    """Why the project cannot speak *languages* on the kit it builds with — it needs :data:`MIN_KIT_VERSION`."""
    if not is_multilingual(languages) or not source_path:
        return None
    version = kit_version(source_path)
    if kit_speaks_languages(version):
        return None
    return (
        f"Jazyky aplikácie pozná šablóna od verzie nex-app-kit {MIN_KIT_VERSION}, projekt stojí na "
        f"{version or 'verzii, ktorá sa nedá prečítať'}. Najprv povýš šablónu aplikácie (tlačidlo pri jazykoch), potom "
        "jazyky ulož."
    )


def of_slug(slug: str) -> tuple[str, ...]:
    """The languages of the project with *slug* (``projects.languages``) — for the steps that know a project only by its
    slug (a build turn's rules, Verifikácia). A project the cockpit has no row for speaks Slovak."""
    from sqlalchemy import select

    from backend.db.models.projects import Project
    from backend.db.session import SessionLocal

    with SessionLocal() as db:
        found = db.execute(select(Project.languages).where(Project.slug == slug)).scalar_one_or_none()
    return tuple(found or DEFAULT_LANGUAGES)


def installation_language(project_languages: Optional[Sequence[str]], customer_language: Optional[str]) -> str:
    """The language an installation starts in: the customer's when the app speaks it, else the app's main one."""
    languages = tuple(project_languages or DEFAULT_LANGUAGES)
    return customer_language if customer_language in languages else languages[0]


# --- Verifikácia ---------------------------------------------------------------------------------------------------


def _manifest_languages(root: Path) -> tuple[Optional[list], Optional[str]]:
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"chýba {MANIFEST}"
    except (OSError, ValueError):
        return None, f"{MANIFEST} nie je platný JSON"
    if not isinstance(manifest, dict):
        return None, f"{MANIFEST} nie je objekt"
    languages = manifest.get("languages", list(DEFAULT_LANGUAGES))
    return (languages if isinstance(languages, list) else None), None


def static_problems(root: Path, languages: Sequence[str]) -> list[str]:
    """What the files of the project say against its *languages* — no tool runs: the manifest, the kit's release, a
    catalog per language and the app's own guard of texts. One Slovak sentence per problem."""
    root = Path(root)
    problems: list[str] = []
    declared, unreadable = _manifest_languages(root)
    if unreadable:
        problems.append(unreadable)
    elif declared != list(languages):
        said = ", ".join(str(c) for c in declared) if declared else "nič"
        problems.append(
            f"{MANIFEST} hovorí o jazykoch „{said}“, projekt má hovoriť: {describe(languages)} — pole `languages` "
            f"v manifeste je [{', '.join(json.dumps(c) for c in languages)}], hlavný jazyk prvý"
        )
    version = kit_version(str(root))
    if not kit_speaks_languages(version):
        problems.append(
            f"aplikácia stojí na nex-app-kit {version or '(verzia sa nedá prečítať)'} — jazyky pozná šablóna od "
            f"{MIN_KIT_VERSION}; šablónu povýši Manažér v nastaveniach projektu"
        )
    for code in languages:
        if not (root / CATALOG_DIR / f"{code}.ts").is_file():
            problems.append(f"chýba katalóg textov pre jazyk {name_of(code)} — {CATALOG_DIR}/{code}.ts")
    if len(languages) > 1 and not (root / TEXTS_TEST).is_file():
        problems.append(f"chýba stráž textov aplikácie {TEXTS_TEST} (zo šablóny nex-app-kit)")
    return problems


def _fetch_checker(version: str, destination: Path) -> None:
    if not _SEMVER.match(version):
        raise CheckNotRun(f"verzia nex-app-kit „{version}“ nie je číslo vydania")
    url = CHECKER_URL.format(version=version)
    try:
        with urllib.request.urlopen(url, timeout=settings.app_texts_fetch_timeout_seconds) as response:  # noqa: S310
            destination.write_bytes(response.read())
    except OSError as exc:
        raise CheckNotRun(f"kontrolu textov šablóny nex-app-kit v{version} sa nepodarilo stiahnuť z GitHubu") from exc


def tool_problems(root: Path, languages: Sequence[str], version: str) -> list[str]:
    """What the TypeScript parser finds: a text of the main catalog missing in another one, and a text written into the
    code — the latter by the kit's own checker at *version*. It only parses the project's files, never runs them."""
    root = Path(root)
    with tempfile.TemporaryDirectory(prefix="app-texts-") as tmp:
        checker = Path(tmp) / "check-texts.mjs"
        _fetch_checker(version, checker)
        argv = [
            "node",
            str(RUNNER),
            str(checker),
            str(root / "frontend"),
            ",".join(languages),
            str(settings.app_texts_max_source_bytes),
        ]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(_TOOLS_DIR),
                capture_output=True,
                text=True,
                timeout=settings.app_texts_check_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CheckNotRun("nástroj kontroly sa nepodarilo spustiť") from exc
    if proc.returncode != 0:
        logger.warning("app texts check failed to run: %s", proc.stderr[-2000:])
        raise CheckNotRun("nástroj kontroly skončil chybou (je zapísaná v logu kokpitu)")
    try:
        report = json.loads(proc.stdout)
    except ValueError as exc:
        raise CheckNotRun("nástroj kontroly vrátil nečitateľný výsledok") from exc
    problems: list[str] = []
    for code, keys in report.get("missing", {}).items():
        shown = ", ".join(f"„{k}“" for k in keys[:20]) + (f" a ďalších {len(keys) - 20}" if len(keys) > 20 else "")
        problems.append(f"v jazyku {name_of(code)} chýba preklad textov {shown} ({CATALOG_DIR}/{code}.ts)")
    for code, keys in report.get("extra", {}).items():
        shown = ", ".join(f"„{k}“" for k in keys[:20])
        problems.append(
            f"katalóg {CATALOG_DIR}/{code}.ts má texty, ktoré hlavný katalóg nemá: {shown} — kľúč vzniká "
            "v hlavnom jazyku"
        )
    for code, why in report.get("unreadable", {}).items():
        problems.append(f"katalóg {CATALOG_DIR}/{code}.ts sa nedá prečítať — {why}")
    hard = report.get("hard", [])
    if hard:
        shown = "; ".join(hard[:15]) + (f"; a ďalších {len(hard) - 15}" if len(hard) > 15 else "")
        problems.append(f"texty napísané priamo v kóde namiesto katalógov ({len(hard)}): {shown}")
    return problems


def problems(root: Path, languages: Sequence[str]) -> tuple[list[str], list[str]]:
    """Verifikácia's measurement of a multilingual project: ``(problems, not_run)`` — the problems stop the version, the
    sentences in ``not_run`` say which part could not be measured and why."""
    found = static_problems(root, languages)
    version = kit_version(str(root))
    if len(languages) < 2 or not kit_speaks_languages(version):
        return found, []
    try:
        return found + tool_problems(root, languages, version or ""), []
    except CheckNotRun as exc:
        return found, [f"{CHECK_NAME}: preklady a texty v kóde sa nezmerali — {str(exc).rstrip('.')}."]
