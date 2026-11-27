"""DEV-523 — the Web addendum of the agents' charters tells the Staviteľ of a Web project on the app template that the
web's accounts, sign-in by e-mail and the registration the admin decides come from the template (WebApp, the kit's
/web/auth and /web-accounts) — the agent builds the web's screens only, not a second sign-in."""

from __future__ import annotations

from pathlib import Path

ADDENDUM = Path(__file__).resolve().parents[1] / "templates" / "web-project-charter.md"


def test_the_staviteľ_of_a_web_project_on_the_template_builds_only_its_screens() -> None:
    text = ADDENDUM.read_text(encoding="utf-8")
    start = text.index("**Na šablóne aplikácií (nex-app-kit od v0.23.0, DEV-523) dáva web šablóna — nestavaj ho.**")
    rule = " ".join(text[start : text.index("\n\n", start)].split())

    for said in ("`WebApp`", "`useWebAccount()`", "`kit.web_account`", "`/web/auth/*`", "`/web-accounts`"):
        assert said in rule
    assert "Registrácia je žiadosť, ktorú schvaľuje správca — nie samoregistrácia." in rule
    assert "Body o verejnosti nižšie platia len pre projekt bez šablóny." in rule
