"""DEV-415 — the Staviteľ of a multilingual app uses what nex-app-kit 0.16.0 brings instead of building it again.

16.11.2026 the Specification of NEX Manager 1.7.0 planned a button, two server routes and a store of its own so that the
admin changes a person's language, a translation of its own server sentences in its screens, and the notes and the
guide only in Slovak — the template could do none of it, and the cockpit's rule told the Staviteľ to write the server's
sentences in the main language. nex-app-kit 0.16.0 does all of it; the rules say how. Director: „Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import create_project_postscaffold as postscaffold

SKILL = Path(__file__).resolve().parents[1] / "templates" / "skills" / "sablona-aplikacie" / "SKILL.md"


@pytest.fixture(scope="module")
def rules() -> str:
    return postscaffold.languages_rules(["sk", "hu", "en"])


@pytest.mark.parametrize(
    "sentence",
    [
        "kľúče `app.server.…` v katalógoch `backend/app/locales/<jazyk>.json`",
        'odmietnutie je `KitError(409, "APP-…", "app.server.…",\n  params={…})`',
        "`RELEASE_NOTES.<jazyk>.md`, `docs/AKO_ZACAT.<jazyk>.md`",
        "vlastné tlačidlo, cestu servera ani úložisko na to nestavaj",
    ],
)
def test_the_staviteľs_rules_say_how_the_kit_speaks_the_persons_language(rules, sentence):
    assert sentence in rules


def test_the_rules_no_longer_keep_the_servers_sentences_in_the_main_language_on_the_new_kit(rules):
    assert "Hlášky servera aplikácie (`backend/app/`) píšeš v hlavnom jazyku;" not in rules
    assert "Na staršej šablóne ich píšeš v hlavnom jazyku." in rules


@pytest.mark.parametrize(
    "sentence",
    [
        '"app.server.areaTaken": "Oblasť „{name}“ už existuje."',
        "`server_messages=load_server_messages(…)`",
        "`tr(request_language(request), kľúč, hodnoty)`",
        "`RELEASE_NOTES.hu.md`, `docs/AKO_ZACAT.en.md`",
    ],
)
def test_the_template_skill_shows_how(sentence):
    assert sentence in SKILL.read_text(encoding="utf-8")
