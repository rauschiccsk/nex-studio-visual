"""DEV-395 — an app on the template speaks the languages of its project, and the cockpit measures it.

15.11.2026 the Director wanted Orthodox Hermenia's screens in Hungarian and the cockpit built only Slovak apps:
„Viacjazyčné aplikácie budeme potrebovať … každý užívateľ môže nastaviť pre seba jazyk aplikácie.“ To the direction
and the tickets: „Súhlasím“, „Áno založ všetky štyri a začni ich implementovať.“

What is guarded here, without a database: the languages a project may choose, what reaches the scaffold, the
installation and the agents' rules, the idea's brief, and the measurement Verifikácia stops a version on — the manifest,
the kit's release, a catalog per language, every translation and no text written into the code (the real runner beside
the cockpit's pinned TypeScript parser, as the image runs it).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from backend.schemas.project import ProjectCreate
from backend.services import app_languages, app_template, uat_provisioner
from backend.services import create_project_postscaffold as postscaffold
from backend.services.poradca import handoff

KIT_OK = "0.14.1"
KIT_OLD = "0.13.0"

# A stand-in for the kit's checker (bin/check-texts.mjs) with its interface — `sourcesUnder` and `hardTexts` — so the
# runner is measured here without GitHub: every JSX text with a letter in it is a text written into the code.
FAKE_CHECKER = r"""
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
export function sourcesUnder(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "locales" ? [] : sourcesUnder(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}
export function hardTexts(name, code) {
  return [...code.matchAll(/>([^<>{}]*\p{L}[^<>{}]*)</gu)].map((m) => [1, "JSX text", m[1].trim()]);
}
"""


def _project(root: Path, *, languages: list[str] | None, kit: str = KIT_OK, catalogs: dict[str, str] | None = None):
    """A project on the template as the scaffold leaves it: manifest, the kit on both sides, catalogs, texts guard."""
    manifest = {"template": "nsv-standard", "name": "Hermenia"}
    if languages is not None:
        manifest["languages"] = languages
    (root / "frontend" / "src" / "locales").mkdir(parents=True)
    (root / "frontend" / "src" / "test").mkdir(parents=True)
    (root / "backend").mkdir(parents=True)
    (root / "frontend" / "app.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/nex-app-kit": {"version": kit}}}), encoding="utf-8"
    )
    (root / "backend" / "pyproject.toml").write_text(
        f'[project]\ndependencies = ["{app_template.kit_server_dependency(kit)}"]\n', encoding="utf-8"
    )
    (root / "frontend" / "src" / "test" / "texts.test.ts").write_text("// the scaffold's guard\n", encoding="utf-8")
    for code, body in (catalogs or {}).items():
        (root / "frontend" / "src" / "locales" / f"{code}.ts").write_text(body, encoding="utf-8")
    return root


HU = 'export const hu = {\n  "app.title": "Hermenia",\n  "app.save": "Mentés",\n} satisfies Record<string, Message>;\n'
SK_FULL = (
    'export const sk: Record<AppMessageKey, Message> = {\n  "app.title": "Hermenia",\n  "app.save": "Uložiť",\n};\n'
)


# --- what a project may choose ------------------------------------------------------------------------------------


def test_known_languages_are_accepted_and_the_rest_is_said():
    assert app_languages.refusal(["hu", "sk"]) is None
    assert app_languages.refusal(["sk"]) is None
    assert app_languages.refusal([]) == "Aplikácia musí hovoriť aspoň jedným jazykom — vyber hlavný jazyk."
    assert app_languages.refusal(["hu", "hu"]) == "Každý jazyk aplikácie môže byť vybraný len raz."
    assert app_languages.refusal(["de"]) == (
        "Jazyk „de“ šablóna aplikácie nepozná — pozná tieto: slovenčina (sk), maďarčina (hu), angličtina (en)."
    )


def test_more_than_slovak_needs_the_template():
    base = {"name": "Hermenia", "slug": "orthodox-hermenia", "type": "standard", "auth_mode": "password",
            "description": ""}  # fmt: skip

    assert ProjectCreate(**base, template="nsv-standard", languages=["hu", "sk"]).languages == ["hu", "sk"]
    assert ProjectCreate(**base).languages == ["sk"]
    with pytest.raises(ValueError, match="Inými jazykmi než slovenčinou hovorí len aplikácia na šablóne aplikácie"):
        ProjectCreate(**base, languages=["hu"])
    with pytest.raises(ValueError, match="šablóna aplikácie nepozná"):
        ProjectCreate(**base, template="nsv-standard", languages=["de"])


def test_each_language_comes_with_its_release_of_the_kit():
    """DEV-399 — English came with nex-app-kit 0.15.0, Slovak and Hungarian with 0.14.1."""
    assert app_languages.kit_needed(("sk", "hu")) == "0.14.1"
    assert app_languages.kit_needed(("sk", "hu", "en")) == "0.15.0"
    assert app_languages.kit_knows("0.14.1") == ["sk", "hu"]
    assert app_languages.kit_knows("0.15.0") == ["sk", "hu", "en"]
    assert app_languages.kit_knows("0.13.0") == [] and app_languages.kit_knows(None) == []
    assert set(app_languages.KIT_SINCE) == set(app_languages.LANGUAGES)  # every language says its release
    assert app_languages.NEW_PROJECT_LANGUAGES == ("sk", "hu", "en")


def test_an_english_app_on_a_kit_before_english_is_stopped(tmp_path):
    root = _project(tmp_path, languages=["sk", "hu", "en"], kit="0.14.1", catalogs={"sk": HU, "hu": HU, "en": HU})

    assert app_languages.static_problems(root, ("sk", "hu", "en")) == [
        "aplikácia stojí na nex-app-kit 0.14.1 — jazyky slovenčina, maďarčina, angličtina pozná šablóna od 0.15.0; "
        "šablónu povýši Manažér v nastaveniach projektu"
    ]
    rules = postscaffold.project_type_rules("standard", "nsv-standard", ("hu", "sk", "en"))
    assert "a šablóna nex-app-kit je aspoň 0.15.0" in rules


def test_an_installation_starts_in_the_customers_language_when_the_app_speaks_it():
    assert app_languages.installation_language(["hu", "sk"], "sk") == "sk"
    assert app_languages.installation_language(["hu", "sk"], None) == "hu"
    assert app_languages.installation_language(["hu", "sk"], "de") == "hu"
    assert app_languages.describe(["hu", "sk"]) == "maďarčina (hlavný jazyk), slovenčina"


# --- the scaffold, the installation, the agents --------------------------------------------------------------------


def _values(languages: tuple[str, ...]) -> dict:
    return app_template.scaffold_values(
        name="Hermenia", slug="orthodox-hermenia", auth_mode="password", ai=False, ports=(1, 2, 3), version=KIT_OK,
        languages=languages,
    )  # fmt: skip


def test_only_a_multilingual_scaffold_names_its_languages():
    assert _values(("hu", "sk"))["languages"] == ["hu", "sk"]
    assert _values(("hu",))["languages"] == ["hu"]
    # A kit release before 0.14.1 refuses a key it does not know; a Slovak app's scaffold stays what it was.
    assert "languages" not in _values(("sk",))


def test_the_installation_is_told_its_language_only_when_given():
    compose = yaml.safe_load("services:\n  backend:\n    environment:\n      LOG_LEVEL: info\n")

    told = uat_provisioner.with_installation_identity(
        compose, "backend", customer_name="SKIT o.z.", environment="uat", language="hu"
    )
    slovak = uat_provisioner.with_installation_identity(
        compose, "backend", customer_name="SKIT o.z.", environment="uat"
    )

    assert told["services"]["backend"]["environment"]["NEX_LANGUAGE"] == "hu"
    assert "NEX_LANGUAGE" not in slovak["services"]["backend"]["environment"]


def test_the_agents_read_the_languages_with_every_token_filled():
    rules = postscaffold.project_type_rules("standard", "nsv-standard", ("hu", "sk"))

    assert "## Aplikácia hovorí jazykmi projektu (DEV-395)" in rules
    assert "Aplikácia hovorí: **maďarčina (hlavný jazyk), slovenčina**." in rules
    assert '`"languages": ["hu", "sk"]`' in rules
    assert "Hlavný katalóg (`hu.ts`)" in rules
    assert "aspoň 0.14.1" in rules
    assert "{{" not in rules


def test_a_slovak_project_and_one_without_the_template_read_no_languages_rules():
    assert "jazykmi projektu" not in postscaffold.project_type_rules("standard", "nsv-standard", ("sk",))
    assert "jazykmi projektu" not in postscaffold.project_type_rules("standard", None, ("hu", "sk"))
    assert postscaffold.project_type_rules("standard", "nsv-standard") == postscaffold.project_type_rules(
        "standard", "nsv-standard", ("sk",)
    )


def test_the_projektant_names_the_languages_in_the_brief():
    head = "meno: Hermenia\nslug: orthodox-hermenia\njazyky: hu, sk\ndruh: dar\n---\n" + "Zadanie prvej verzie. " * 10

    brief = handoff.project_brief(f"<zadanie-projektu>\n{head}\n</zadanie-projektu>")
    unknown = handoff.project_brief(f"<zadanie-projektu>\n{head.replace('hu, sk', 'hu, de')}\n</zadanie-projektu>")

    assert brief is not None and brief.languages == ["hu", "sk"]
    assert unknown is not None and unknown.languages is None  # a line the cockpit cannot read whole is left out


# --- Verifikácia ---------------------------------------------------------------------------------------------------


def test_a_whole_project_has_no_static_problem(tmp_path):
    root = _project(tmp_path, languages=["hu", "sk"], catalogs={"hu": HU, "sk": SK_FULL})

    assert app_languages.static_problems(root, ("hu", "sk")) == []


def test_each_static_problem_is_said_in_a_sentence(tmp_path):
    root = _project(tmp_path, languages=None, kit=KIT_OLD, catalogs={"hu": HU})
    (root / "frontend" / "src" / "test" / "texts.test.ts").unlink()

    assert app_languages.static_problems(root, ("hu", "sk")) == [
        "frontend/app.manifest.json hovorí o jazykoch „sk“, projekt má hovoriť: maďarčina (hlavný jazyk), slovenčina "
        '— pole `languages` v manifeste je ["hu", "sk"], hlavný jazyk prvý',
        "aplikácia stojí na nex-app-kit 0.13.0 — jazyky maďarčina, slovenčina pozná šablóna od 0.14.1; šablónu povýši "
        "Manažér v nastaveniach projektu",
        "chýba katalóg textov pre jazyk slovenčina — frontend/src/locales/sk.ts",
        "chýba stráž textov aplikácie frontend/src/test/texts.test.ts (zo šablóny nex-app-kit)",
    ]


@pytest.fixture()
def fake_checker(monkeypatch):
    if shutil.which("node") is None or not (app_languages._TOOLS_DIR / "node_modules" / "typescript").is_dir():
        pytest.fail("the cockpit's TypeScript tools are not installed (templates/dead-code: npm ci) — CI installs them")

    def fetch(version: str, destination: Path) -> None:
        assert version == KIT_OK  # the checker of the very release the project builds with
        destination.write_text(FAKE_CHECKER, encoding="utf-8")

    monkeypatch.setattr(app_languages, "_fetch_checker", fetch)


def test_a_missing_translation_and_a_text_in_the_code_stop_the_version(tmp_path, fake_checker):
    sk = (
        'export const sk: Record<AppMessageKey, Message> = {\n  "app.title": "Hermenia",\n'
        '  "app.extra": "Navyše",\n};\n'
    )
    root = _project(tmp_path, languages=["hu", "sk"], catalogs={"hu": HU, "sk": sk})
    (root / "frontend" / "src" / "pages").mkdir()
    (root / "frontend" / "src" / "pages" / "Home.tsx").write_text("export const Home = () => <p>Mentés most</p>;\n")
    # A link in the checkout leads the reading nowhere — what lies behind it is never read.
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "Secret.tsx").write_text("export const Secret = () => <p>Mimo projektu</p>;\n")
    (root / "frontend" / "src" / "pages" / "elsewhere").symlink_to(outside)

    problems, not_run = app_languages.problems(root, ("hu", "sk"))

    assert not_run == []
    assert problems == [
        "v jazyku slovenčina chýba preklad textov „app.save“ (frontend/src/locales/sk.ts)",
        "katalóg frontend/src/locales/sk.ts má texty, ktoré hlavný katalóg nemá: „app.extra“ — kľúč vzniká v hlavnom "
        "jazyku",
        "texty napísané priamo v kóde namiesto katalógov (1): frontend/src/pages/Home.tsx:1 (JSX text): Mentés most",
    ]


def test_complete_catalogs_and_no_text_in_the_code_pass(tmp_path, fake_checker):
    root = _project(tmp_path, languages=["hu", "sk"], catalogs={"hu": HU, "sk": SK_FULL})
    (root / "frontend" / "src" / "pages").mkdir()
    (root / "frontend" / "src" / "pages" / "Home.tsx").write_text('export const Home = () => <p>{t("app.save")}</p>;\n')

    assert app_languages.problems(root, ("hu", "sk")) == ([], [])


def test_a_catalog_the_cockpit_cannot_read_is_said(tmp_path, fake_checker):
    composed = 'export const sk: Record<AppMessageKey, Message> = {\n  ...other,\n  "app.title": "Hermenia",\n};\n'
    root = _project(tmp_path, languages=["hu", "sk"], catalogs={"hu": HU, "sk": composed})

    problems, _ = app_languages.problems(root, ("hu", "sk"))

    assert problems == [
        'katalóg frontend/src/locales/sk.ts sa nedá prečítať — text na riadku 2 nie je v tvare "kľúč": text'
    ]


def test_a_checker_that_cannot_be_fetched_says_so_and_keeps_the_static_problems(tmp_path, monkeypatch):
    root = _project(tmp_path, languages=["hu"], catalogs={"hu": HU, "sk": SK_FULL})

    def unreachable(version: str, destination: Path) -> None:
        raise app_languages.CheckNotRun("kontrolu textov šablóny nex-app-kit v0.14.1 sa nepodarilo stiahnuť z GitHubu")

    monkeypatch.setattr(app_languages, "_fetch_checker", unreachable)

    problems, not_run = app_languages.problems(root, ("hu", "sk"))

    assert problems[0].startswith("frontend/app.manifest.json hovorí o jazykoch „hu“")
    assert not_run == [
        "Kontrola jazykov aplikácie: preklady a texty v kóde sa nezmerali — kontrolu textov šablóny nex-app-kit "
        "v0.14.1 sa nepodarilo stiahnuť z GitHubu."
    ]


def test_a_version_number_from_the_checkout_never_reaches_a_url(tmp_path):
    with pytest.raises(app_languages.CheckNotRun, match="nie je číslo vydania"):
        app_languages._fetch_checker("0.14.1/../../evil", tmp_path / "x.mjs")
