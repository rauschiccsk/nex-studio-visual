"""DEV-259 — the agent of a project on an app template reads what the frame check before Overenie asks about.

30.10.2026 the skill said only „Hotové je, keď kontrola rámu pred Overením nič nenájde.“ The agent of NEX Knowledge
0.2.0 guessed in its design that the check may want the skeleton's module-level ``kit`` object and planned a spare one;
the check never asks that. The skill now names what the check asks — and the lists in it are the lists the check
reads, so a change of the check that the skill does not follow turns this red.
"""

from __future__ import annotations

import re

from backend.services import create_project_postscaffold as cps
from backend.services import orchestrator

SKILL = (cps.NEX_STUDIO_TEMPLATES / "skills" / "sablona-aplikacie" / cps.SKILL_FILE).read_text(encoding="utf-8")


def _named(label: str) -> set[str]:
    """The names in backticks the skill lists after *label*, up to the dash that ends the list."""
    found = re.search(re.escape(label) + r"(.*?) — ", SKILL, re.DOTALL)
    assert found, f"the skill has no list „{label}“"
    return set(re.findall(r"`([^`]+)`", found.group(1)))


def test_the_skill_names_every_frame_component_the_check_refuses():
    assert _named("Komponenty rámu z nex-shared:") == set(orchestrator._TEMPLATE_FRAME_COMPONENTS)


def test_the_skill_names_every_frame_page_the_check_refuses():
    assert _named("Stránky rámu:") == set(orchestrator._TEMPLATE_FRAME_PAGES)


def test_the_skill_names_every_segment_of_the_standard_interface():
    assert _named("Úseky ciest servera šablóny:") == set(orchestrator._TEMPLATE_API_SEGMENTS)


def test_the_check_refuses_a_route_on_each_page_the_skill_names_and_nothing_else():
    for page in orchestrator._TEMPLATE_FRAME_PAGES:
        assert orchestrator._TEMPLATE_FRAME_ROUTE_RE.findall(f'<Route path="{page}" element={{<X />}} />') == [page]
    assert orchestrator._TEMPLATE_FRAME_ROUTE_RE.findall('<Route path="/settings/system" />') == ["/settings/system"]
    for own in ("/admin", "/admin/settings", "/loginx", "/updates/2", "/areas"):
        assert orchestrator._TEMPLATE_FRAME_ROUTE_RE.findall(f'<Route path="{own}" />') == [], own


def test_the_skill_says_where_the_kit_is_built_does_not_matter():
    assert "Kde — na úrovni modulu alebo vo funkcii, napríklad `build_kit` — je jedno." in SKILL
    server = (
        "def build_kit(settings):\n    return AppKit(manifest=m)\n\ndef create_app():\n    build_kit(s).install(app)\n"
    )
    assert orchestrator._stands_on_app_kit(server) == (True, True)
