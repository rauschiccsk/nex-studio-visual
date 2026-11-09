"""DEV-327 — Poradca sees what a build is for: the version's brief and the DEV tickets it names, however old.

09.11.2026 NEX Inbox 1.10.3 stood on the card „Auditor po 5 kolách stále FAIL“ and the Director asked Poradca whether
the finding holds and how to solve it. Poradca read the code well, but it did not know the quick fix belongs to DEV-167,
where Andros Dedo had written the measurement of the CI runner — so it suspected what the measurement had ruled out.
The tool ``stavba`` showed only the newest 50 messages of the build and the brief (the kickoff naming DEV-167) was
older. Director: „Chcem aby sme upravili kokpit tak, aby som mohol spoľahnúť na Poradcu.“ Now ``stavba`` always shows
the brief and the DEV tickets it or the version names, and the charter tells Poradca to read them before advising on a
stopped build — and to ask the Manažér, not the agent, before a rule of the specification changes.
"""

from __future__ import annotations

from pathlib import Path

from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.services.poradca import tools
from tests.test_poradca_tools_db import world  # noqa: F401 — the fixture is used by name

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"

BRIEF = (
    "Rýchla oprava (DEV-167): úloha smb-blackhole na samostatný vykonávač kontrol s plnými právami.\n\n"
    "Prečo (tiket DEV-167): vykonávače kontrol na ANDROSe odteraz púšťajú Docker bez práv správcu; súvisí aj dev-12."
)


def _stopped_build(db, version) -> None:
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="fast_fix",
            current_stage="verifikacia",
            current_actor="auditor",
            status="blocked",
            block_reason="decision_needed",
            next_action="Auditor po 5 kolách stále FAIL — rozhodni (Decision Card): usmerni opravu, alebo podrž.",
        )
    )


def _message(db, version, content: str, *, kind: str = "verdict", author: str = "auditor") -> None:
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="priprava" if kind == "kickoff" else "verifikacia",
            author=author,
            recipient="ai_agent" if kind == "kickoff" else "manazer",
            kind=kind,
            content=content,
        )
    )
    db.flush()


def _tickets_line(out: str) -> str:
    found = [line for line in out.splitlines() if line.startswith("Tikety DEV tejto verzie:")]
    return found[0] if found else ""


async def test_the_brief_and_its_tickets_stay_in_view_after_many_newer_messages(world):  # noqa: F811
    db, version = world["db"], world["version"]
    _stopped_build(db, version)
    _message(db, version, BRIEF, kind="kickoff", author="manazer")
    for n in range(tools._MESSAGES_LIMIT + 10):
        _message(db, version, f"Kolo {n}: kontrola výpadku disku zlyhala.")

    out = await world["tools"].stavba({})

    assert "Zadanie verzie (ako Manažér stavbu spustil, " in out
    assert BRIEF in out
    assert _tickets_line(out) == (
        "Tikety DEV tejto verzie: DEV-167, DEV-12. Ľudia a Dedo do nich zapisujú merania a rozhodnutia — pred radou "
        "k zastavenej stavbe ich prečítaj nástrojom plane_tiket aj s komentármi."
    )


async def test_the_ticket_the_version_has_in_plane_is_named_first(world):  # noqa: F811
    db, version = world["db"], world["version"]
    version.plane_identifier = "DEV-300"
    _stopped_build(db, version)
    _message(db, version, BRIEF, kind="kickoff", author="manazer")

    out = await world["tools"].stavba({})

    assert _tickets_line(out).startswith("Tikety DEV tejto verzie: DEV-300, DEV-167, DEV-12. ")


async def test_a_ticket_of_another_register_is_not_named_the_cockpit_does_not_read_it(world):  # noqa: F811
    db, version = world["db"], world["version"]
    _stopped_build(db, version)
    _message(db, version, "Oprava podľa MAGER-66: preklad hlášky.", kind="kickoff", author="manazer")

    out = await world["tools"].stavba({})

    assert "Oprava podľa MAGER-66: preklad hlášky." in out
    assert _tickets_line(out) == ""


async def test_a_long_brief_is_cut_with_a_mark_and_a_ticket_at_its_end_still_named(world):  # noqa: F811
    db, version = world["db"], world["version"]
    _stopped_build(db, version)
    head = "Požiadavka. " * (tools._BRIEF_CHARS // 12 + 50)
    _message(db, version, head + "Tiket DEV-88.", kind="kickoff", author="manazer")

    out = await world["tools"].stavba({})

    assert head[: tools._BRIEF_CHARS].strip() + " …" in out
    assert "Tiket DEV-88." not in out
    assert _tickets_line(out).startswith("Tikety DEV tejto verzie: DEV-88. ")


async def test_the_newest_brief_counts_when_the_build_started_again(world):  # noqa: F811
    db, version = world["db"], world["version"]
    _stopped_build(db, version)
    _message(db, version, "Prvé zadanie (DEV-1).", kind="kickoff", author="manazer")
    _message(db, version, "Nové zadanie (DEV-2).", kind="kickoff", author="manazer")

    out = await world["tools"].stavba({})

    assert "Nové zadanie (DEV-2)." in out.split("Posledných")[0]
    assert _tickets_line(out).startswith("Tikety DEV tejto verzie: DEV-2. ")


async def test_a_build_without_a_brief_shows_no_brief(world):  # noqa: F811
    db, version = world["db"], world["version"]
    _stopped_build(db, version)
    _message(db, version, "Kolo 1: zlyhalo.")

    out = await world["tools"].stavba({})

    assert "Zadanie verzie" not in out and _tickets_line(out) == ""


def test_the_charter_tells_poradca_to_read_the_tickets_and_to_ask_before_a_rule_changes():
    text = " ".join(CHARTER.read_text(encoding="utf-8").split())

    assert (
        "Keď stavba stojí na verdikte Audítora alebo na karte rozhodnutia, pred radou prečítaj nástrojom `plane_tiket` "
        "každý tiket, ktorý `stavba` menuje pri zadaní verzie, aj s komentármi." in text
    )
    assert "Zmerané v nich má prednosť pred tvojou úvahou" in text
    assert (
        "Pokyn, ktorý by zmenil pravidlo zapísané v Špecifikácii (limit, strop, pravidlo stavu či správania), "
        "agentovi nedávaj — o takej zmene rozhoduje Manažér." in text
    )
