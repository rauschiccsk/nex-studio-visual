"""The documents we SHIP must teach the engine's actual contract (audit 2026-07-28).

Charters and the universal ``CLAUDE.md`` are scaffolded verbatim into every new project
(:mod:`backend.services.create_project_postscaffold`), so a stale enum or a missing phase in
``templates/`` is inherited by every project founded from then on — and nothing fails loudly:
the agent simply reports a phase it was never told about, or never reaches an escalation path
its charter does not mention.

These tests pin the shipped docs to the engine's single sources of truth
(``STAGE_VALUES`` / ``STAGES`` / ``BLOCK_KINDS``) so the two cannot drift again. When a phase or
kind is added to the engine, these fail until the charters are updated too — which is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.db.models.pipeline import STAGE_VALUES
from backend.services.create_project_postscaffold import _UNIVERSAL_CLAUDE_MD, _V2_AGENTS
from backend.services.pipeline_status import _AWAITING, BLOCK_KINDS, STAGES

_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

#: The charters that are actually SHIPPED into a new project (the retired v1 coordinator charter
#: is deliberately NOT here — see :func:`test_coordinator_charter_is_not_shipped_and_is_marked_retired`).
_SHIPPED_CHARTERS = sorted(charter for charter, _settings in _V2_AGENTS.values())


def _read(name: str) -> str:
    return (_TEMPLATES / name).read_text(encoding="utf-8")


def _documented_set(doc: str, field: str) -> set[str]:
    """Extract a charter's closed enum set for ``field`` — the `` `field` ∈ `{a, b, c}` `` notation.

    Tolerates the set wrapping across lines (both charters hard-wrap mid-set)."""
    m = re.search(rf"`{field}`\s*∈\s*`\{{(.*?)\}}`", doc, re.DOTALL)
    assert m is not None, f"charter does not declare a `{field}` enum set at all"
    return {tok.strip() for tok in m.group(1).split(",") if tok.strip()}


def test_engine_pipeline_has_five_phases_plus_done() -> None:
    """Anchors the other tests: Vizuál sits between Návrh and Programovanie."""
    assert STAGE_VALUES == ("priprava", "navrh", "vizual", "programovanie", "verifikacia", "done")
    assert set(STAGE_VALUES) == STAGES


@pytest.mark.parametrize("charter", _SHIPPED_CHARTERS)
def test_shipped_charter_stage_set_matches_engine(charter: str) -> None:
    """A charter that omits ``vizual`` leaves the agent no valid way to report the Vizuál phase —
    it reports a neighbouring phase instead and the cockpit shows the wrong one."""
    assert _documented_set(_read(charter), "stage") == STAGES


@pytest.mark.parametrize("charter", _SHIPPED_CHARTERS)
def test_shipped_charter_kind_set_matches_engine(charter: str) -> None:
    """Omitting ``framework_issue`` / ``consultation`` from the closed set makes those escalation
    paths unreachable — the agent never emits a kind its charter forbids."""
    assert _documented_set(_read(charter), "kind") == BLOCK_KINDS


@pytest.mark.parametrize("charter", _SHIPPED_CHARTERS)
def test_shipped_charter_awaiting_set_matches_engine(charter: str) -> None:
    assert _documented_set(_read(charter), "awaiting") == _AWAITING


@pytest.mark.parametrize("charter", _SHIPPED_CHARTERS)
def test_shipped_charter_does_not_promise_four_phases(charter: str) -> None:
    assert "4-fázový" not in _read(charter)


def test_universal_claude_md_teaches_all_five_phases() -> None:
    """The CLAUDE.md written into EVERY new project — its phase list is the contract a project
    inherits at founding."""
    doc = _read(_UNIVERSAL_CLAUDE_MD)
    for label in ("Príprava", "Návrh", "Vizuál", "Programovanie", "Verifikácia"):
        assert label in doc, f"universal CLAUDE.md never mentions the {label} phase"
    assert "## 2. Fázy (Príprava → Návrh → Vizuál → Programovanie → Verifikácia)" in doc


def test_coordinator_charter_is_not_shipped_and_is_marked_retired() -> None:
    """The v1 Koordinátor role is retired (CR-V2-005). The charter survives only as history, so it
    must (a) never be scaffolded and (b) say so at the top — its old header claimed the opposite."""
    assert not any("coordinator" in charter for charter, _ in _V2_AGENTS.values())
    assert set(_V2_AGENTS) == {"ai-agent", "auditor"}

    doc = _read("coordinator-charter.md")
    head = doc[: doc.index("## 1.")]
    assert "VYRADENÉ" in head and "NEPOUŽÍVAŤ" in head
    # The retired procedures must be named as retired in the banner, not merely mandated below it.
    for retired in ("Gate E", "Tiborov test", ".dedo-channel"):
        assert retired in head, f"retirement banner does not mention {retired}"


#: Words that turn a mention of a retired procedure into an explicit DENIAL ("there is no X"),
#: which is legitimate — and is how ``project-claude-md.md`` rules out the v1 file bus.
_NEGATIONS = ("žiadny", "žiadne", "žiadna", "nie ", "nikdy", "zrušen", "vyraden", "retired")


@pytest.mark.parametrize("charter", _SHIPPED_CHARTERS + [_UNIVERSAL_CLAUDE_MD])
def test_shipped_docs_carry_no_retired_v1_procedures(charter: str) -> None:
    """Gate E, the Tiborov/Dual-Build test and the ``.dedo-channel`` file bus are all retired — no
    document we scaffold into a new project may MANDATE them. Naming one to rule it out is fine."""
    doc = _read(charter)
    for retired in ("Gate E", "Tiborov", "Dual-Build", ".dedo-channel"):
        for m in re.finditer(re.escape(retired), doc):
            context = doc[max(0, m.start() - 120) : m.end() + 120].lower()
            assert any(neg in context for neg in _NEGATIONS), (
                f"{charter} mandates retired {retired} (mention is not a denial)"
            )
