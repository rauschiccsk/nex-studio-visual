"""The verdict the Manažér reads is ONE message, and it is prose (ICCINT-48, ICCINT-49).

01.09.2026, nex-productcatalogs, Verifikácia PASS — the sentence he reads when deciding to close the version.
Two defects landed on it at once.

**48.** The Auditor mixed its structured-output syntax into the JSON ``summary`` field, and the engine stored
what it was handed:

    …peniaze počíta na cent a vzhľad zostal taký, aký si schválil.</summary>
    <parameter name="findings">["NEBLOKUJÚCE — čistička citlivých údajov…

Nothing was lost — ``payload`` held the parsed ``findings`` correctly — and nothing was untrue. It was simply
unreadable, and it looked like a malfunction.

**49.** The same paragraph appeared twice. The generic status-block writer records every parsed agent block
(an Auditor verdict lands as ``kind='verdict'`` with the rich ``payload['report']``), and the Verifikácia path
then recorded a SECOND message with the same text and the decision fields. Measured: messages 1471 and 1472,
identical 1206-character content. On a PASS that is noise; on a FAIL two identical red paragraphs read as two
separate problems.
"""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

LEAKED = (
    "Verzia je overená. Peniaze počíta na cent a vzhľad zostal taký, aký si schválil."
    '</summary>\n<parameter name="findings">["NEBLOKUJÚCE — čistička citlivých údajov…"]'
)
CLEAN = "Verzia je overená. Peniaze počíta na cent a vzhľad zostal taký, aký si schválil."


def _version(db) -> Version:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Verdict {suffix}",
        slug=f"verdict-{suffix}",
        type="standard",
        auth_mode="password",
        description="Verdict rendering test.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db.add(version)
    db.flush()
    return version


def _verdicts(db, version_id) -> list[PipelineMessage]:
    return list(
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.kind == "verdict")
            .order_by(PipelineMessage.seq)
        )
        .scalars()
        .all()
    )


# ── ICCINT-48: prose, not markup ──────────────────────────────────────────────


def test_machine_markup_never_reaches_the_screen() -> None:
    assert orchestrator._human_text(LEAKED) == CLEAN


def test_a_sentence_without_markup_is_left_alone() -> None:
    """The control. Sanitising must not rewrite text that was fine."""
    for intact in (CLEAN, "Appka sa spustila — 33 tvrdení, 0 chýb.", ""):
        assert orchestrator._human_text(intact) == intact


def test_every_shape_of_the_leak_is_cut() -> None:
    for shape in (
        "Hotovo.</summary>x",
        'Hotovo.<parameter name="f">',
        "Hotovo.<function_calls>",
        'Hotovo.<invoke name="x"',
    ):
        assert orchestrator._human_text(shape) == "Hotovo."


def test_a_message_that_is_nothing_but_markup_is_not_blanked() -> None:
    """An empty bubble tells the Manažér less than the raw text does. Keep something."""
    assert orchestrator._human_text('<parameter name="findings">["x"]') != ""


def test_the_sanitising_happens_at_the_write_chokepoint(db_session) -> None:
    """Not at one caller — every path that records a message goes through it."""
    version = _version(db_session)
    msg = orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content=LEAKED,
        payload={"verdict": "PASS"},
    )
    assert msg.content == CLEAN


# ── ICCINT-49: one message, not two ───────────────────────────────────────────


def test_the_verdict_enriches_the_block_instead_of_repeating_it(db_session) -> None:
    """THE defect. The status-block writer already put this text on screen; the decision fields join it."""
    version = _version(db_session)
    orchestrator._record_message(  # what the generic status-block writer records
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content=LEAKED,
        payload={"report": "## Auditor\n…", "phase": "verifikacia"},
    )
    orchestrator._verdict_message(
        db_session,
        version_id=version.id,
        content=LEAKED,
        payload={"verdict": "PASS", "verified_sha": "abc123", "findings": ["NEBLOKUJÚCE — x"]},
    )

    rows = _verdicts(db_session, version.id)
    assert len(rows) == 1, "the Manažér would read the same paragraph twice"
    # …and nothing was traded away for that: report AND decision fields on one bubble.
    assert rows[0].payload["report"].startswith("## Auditor")
    assert rows[0].payload["verdict"] == "PASS"
    assert rows[0].payload["verified_sha"] == "abc123"
    assert rows[0].content == CLEAN


def test_a_verdict_with_no_block_behind_it_is_still_recorded(db_session) -> None:
    """The engine-built verdict (runtime-floor override) has no agent turn before it. A merge that finds
    nothing must insert, never drop the decision."""
    version = _version(db_session)
    orchestrator._verdict_message(
        db_session, version_id=version.id, content="Verifikácia FAIL.", payload={"verdict": "FAIL"}
    )
    rows = _verdicts(db_session, version.id)
    assert len(rows) == 1 and rows[0].payload["verdict"] == "FAIL"


def test_a_second_round_never_overwrites_the_first_verdict(db_session) -> None:
    """Two rounds can produce the same summary text. The earlier outcome is a record, not a slot to reuse."""
    version = _version(db_session)
    orchestrator._verdict_message(
        db_session, version_id=version.id, content="Verifikácia FAIL.", payload={"verdict": "FAIL"}
    )
    orchestrator._verdict_message(
        db_session, version_id=version.id, content="Verifikácia FAIL.", payload={"verdict": "PASS"}
    )
    rows = _verdicts(db_session, version.id)
    assert [r.payload["verdict"] for r in rows] == ["FAIL", "PASS"], "a round rewrote history"
