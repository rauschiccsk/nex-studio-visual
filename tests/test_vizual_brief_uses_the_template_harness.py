"""DEV-2 — the Vizuál brief tells the agent to USE the preview harness the template already gave the project.

Since ICCINT-107 (10.09.2026) the project template ships the live-preview harness in the shape NEX Manager's agent
designed better than the old template: the decision helper ``frontend/src/preview/isPreview.ts`` (comparison with the
list of enabling values) and, in ``main.tsx``, the two-part condition ``import.meta.env.VITE_PREVIEW &&
isPreviewEnabled()`` — a static guard that keeps MSW and the fixtures out of the release build, and the decision.
The brief kept dictating the old shape („compare with the whole list" in ``main.tsx``) and never named the helper, so
an agent could write its own switch next to it or "simplify" the guard away. Measured 09.10.2026: Career Asistent
has the helper; it enters Vizuál next. Director: „Áno, začni DEV-2".

Pinned here: the brief names what the project really has — use the helper and keep the guard when it is there,
create both in the template's shape when it is not — and the enabling values come from the sandbox's own list.
Every other place that asks "is this the preview?" goes through the helper too: the charter used to dictate
``if (import.meta.env.VITE_PREVIEW) return;`` for ``onUnauthorized``, and Career Asistent wrote exactly that
(``frontend/src/services/api.ts``, measured 09.10.2026) — a release build with ``VITE_PREVIEW=false`` would stop
sending an expired session to the login.
"""

from __future__ import annotations

import re

from backend.services import claude_agent, create_project_postscaffold, orchestrator, vizual_sandbox
from tests.test_orchestrator_v2_vizual import _make_version

HELPER = "frontend/src/preview/isPreview.ts"

#: The flag tested as a whole condition — ``if (import.meta.env.VITE_PREVIEW)`` lets ``"false"`` through (ICCINT-95).
_TRUTHY_FLAG = re.compile(r"\(\s*!?\s*import\.meta\.env\.VITE_PREVIEW\s*\)")


def _brief(db_session, *, with_harness: bool) -> str:
    version, project = _make_version(db_session)
    if with_harness:
        helper = claude_agent.PROJECTS_ROOT / project.slug / HELPER
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("export function isPreviewEnabled() { return false; }\n", encoding="utf-8")
    return orchestrator._vizual_directive(db_session, version.id, None)


def test_a_project_with_the_template_harness_is_told_to_use_it(db_session):
    brief = _brief(db_session, with_harness=True)

    assert "Projekt ho má zo šablóny hotový — POUŽI ho, vlastný prepínač NEPÍŠ" in brief
    assert "`frontend/src/preview/isPreview.ts` (`isPreviewEnabled()`)" in brief
    assert "`import.meta.env.VITE_PREVIEW && isPreviewEnabled()`" in brief
    assert "ponechaj ju presne tak" in brief
    assert "`frontend/src/preview/handlers.ts`" in brief
    assert "vytvor ho" not in brief


def test_a_project_without_it_is_told_to_create_it_in_the_templates_shape(db_session):
    brief = _brief(db_session, with_harness=False)

    assert "Projekt harness zo šablóny NEMÁ — vytvor ho v jej tvare" in brief
    assert "`import.meta.env.VITE_PREVIEW && isPreviewEnabled()`" in brief
    assert "NIE pravdivostne" in brief and "žiadny `.toLowerCase()`" in brief
    assert "nezlučuj ich" in brief
    assert "POUŽI ho" not in brief


def test_both_briefs_name_exactly_the_values_the_sandbox_sends_from(db_session):
    for with_harness in (True, False):
        brief = _brief(db_session, with_harness=with_harness)
        listed = ", ".join(f'"{value}"' for value in vizual_sandbox.PREVIEW_ON)
        assert f"({listed})" in brief


def test_both_briefs_send_every_other_preview_question_through_the_helper(db_session):
    sentence = (
        "kdekoľvek inde, kde kód potrebuje vedieť, či beží náhľad (`onUnauthorized` v API klientovi, strážca "
        "prihlásenia), volaj `isPreviewEnabled()` — nikdy pravdivostne `import.meta.env.VITE_PREVIEW`"
    )
    for with_harness in (True, False):
        assert sentence in _brief(db_session, with_harness=with_harness)


def test_no_text_the_agent_reads_tests_the_flag_truthily(db_session):
    texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(create_project_postscaffold.NEX_STUDIO_TEMPLATES.iterdir())
        if path.is_file() and path.suffix == ".md"
    }
    assert {"agent-shared-base.md", "ai-agent-charter.md", "auditor-charter.md"} <= texts.keys()
    for with_harness in (True, False):
        texts[f"Vizuál brief, harness={with_harness}"] = _brief(db_session, with_harness=with_harness)

    assert sorted(name for name, text in texts.items() if _TRUTHY_FLAG.search(text)) == []


def test_the_charter_silences_the_login_bounce_through_the_helper():
    charter = (create_project_postscaffold.NEX_STUDIO_TEMPLATES / "ai-agent-charter.md").read_text(encoding="utf-8")

    assert "`if (isPreviewEnabled()) return;`" in charter
