"""What is agreed in Vizuál reaches the documents — or stops and asks (ICCINT-29).

Director, 25.08.2026, looking at the nex-productcatalogs Vizuál: *"Vizuál je vynikajúci nástroj aj na to, aby
sme do systému doplnili všetko, čo sme počas návrhu zabudli, nepremysleli do konca a vynechali. Ako to bude
so špecifikáciou?"*

It was not. Approving a Vizuál squashed the screens into one commit and wrote nothing into the documents, so
a build ended carrying two truths that could disagree. Three things broke because of it: the release check is
derived from the Špecifikácia, so whatever lived only in the screens was never checked — the newest and least
thought-through additions were the least verified, which is backwards; the next version reads the
Špecifikácia as the only truth, so those additions would be re-designed differently or silently dropped; and
the task plan was built from "the warm session", a conversation that shortens and is gone.

Three decisions settled it (Director, 02.09.2026): the upfront review runs again but NARROWED to what
changed; a contradiction is a decision card carrying both sides, while filling a silence folds in quietly;
and the fast lane gets no write-back but must say out loud when a fix has left its boundary.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock


def _seed(db) -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Vizual {suffix}",
        slug=f"vizual-{suffix}",
        type="standard",
        auth_mode="password",
        description="Vizuál write-back test.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="vizual",
        current_actor="ai_agent",
        status="awaiting_manazer",
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


def _block(summary: str, findings: list[str] | None = None) -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="vizual",
        kind="gate_report",
        summary=summary,
        awaiting="manazer",
        findings=findings or [],
    )


# ── the brief ─────────────────────────────────────────────────────────────────


def test_the_write_back_brief_names_both_documents_and_forbids_deciding(db_session) -> None:
    version, _state = _seed(db_session)
    brief = orchestrator._vizual_writeback_directive(db_session, version.id)

    assert "specification.md" in brief and "design.md" in brief
    assert orchestrator._VIZUAL_CONFLICT_MARKER in brief
    assert "NEROZHODUJ" in brief, "the agent was not told to leave contradictions alone"
    # Filling a silence is not a contradiction — without this the fold-back drowns in questions.
    assert "NIE JE rozpor" in brief
    # It must not touch the screens: they are approved, only the documents are behind.
    assert "Na obrazovky nesiahaj" in brief


# ── what comes back ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_clean_fold_reports_no_conflict(db_session, monkeypatch) -> None:
    _version, state = _seed(db_session)

    async def _folded(*a, **k):
        return _block("Doplnené do Špecifikácie aj Návrhu.")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _folded)
    assert await orchestrator._writeback_vizual_to_docs(db_session, state) == []


@pytest.mark.asyncio
async def test_a_contradiction_comes_back_with_both_sides(db_session, monkeypatch) -> None:
    """THE decision the agent may not take: which of two decisions taken at different times still holds."""
    _version, state = _seed(db_session)
    both_sides = "Zoznam faktúr — Špecifikácia: zoradený podľa dátumu | Obrazovka: zoradený podľa sumy"

    async def _conflict(*a, **k):
        return _block(f"{orchestrator._VIZUAL_CONFLICT_MARKER} — našiel som jeden rozpor", [both_sides])

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _conflict)
    conflicts = await orchestrator._writeback_vizual_to_docs(db_session, state)
    assert conflicts == [both_sides]


@pytest.mark.asyncio
async def test_a_failed_write_back_never_blocks_the_approval(db_session, monkeypatch) -> None:
    """A missing document the Manažér can SEE is a lesser harm than a cockpit he cannot get past."""
    _version, state = _seed(db_session)

    async def _boom(*a, **k):
        raise RuntimeError("agent nedostupný")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _boom)
    assert await orchestrator._writeback_vizual_to_docs(db_session, state) is None
    # …and it says so, rather than failing silently.
    said = [m for m in _messages(db_session, state.version_id) if (m.payload or {}).get("vizual_writeback")]
    assert said and "nepodarilo" in said[-1].content


@pytest.mark.asyncio
async def test_an_unreadable_answer_is_reported_like_a_crash(db_session, monkeypatch) -> None:
    """From where the Manažér stands, a turn that answered gibberish and a turn that died are the same
    thing: the documents did not get written. It must READ the same too — a silent return here is the
    escape hatch that fails without saying so."""
    from backend.services.pipeline_status import ParseFailure

    _version, state = _seed(db_session)

    async def _gibberish(*a, **k):
        return ParseFailure(reason="no block")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _gibberish)
    assert await orchestrator._writeback_vizual_to_docs(db_session, state) is None
    said = [m for m in _messages(db_session, state.version_id) if (m.payload or {}).get("vizual_writeback")]
    assert said, "the write-back failed and the cockpit said nothing"


@pytest.mark.asyncio
async def test_a_conflict_without_a_description_still_stops_the_build(db_session, monkeypatch) -> None:
    """The agent said ROZPOR but listed nothing. Treating that as clean would let the very case the word
    was invented for slip through."""
    _version, state = _seed(db_session)

    async def _bare(*a, **k):
        return _block(orchestrator._VIZUAL_CONFLICT_MARKER, [])

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _bare)
    assert await orchestrator._writeback_vizual_to_docs(db_session, state) != []


def _messages(db, version_id):
    from sqlalchemy import select

    from backend.db.models.pipeline import PipelineMessage

    return list(db.execute(select(PipelineMessage).where(PipelineMessage.version_id == version_id)).scalars().all())


# ── the card ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_conflict_becomes_a_card_he_can_actually_answer(db_session, monkeypatch) -> None:
    """The build stays AT Vizuál, nothing is signed, and the contradiction arrives as a real Decision Card.

    The card matters as much as the stop. ``DecisionCardsBar`` draws nothing without a decision queue, so a
    hand-written ``decision_needed`` message would block the build behind a bar with NOTHING TO CLICK — the
    cockpit saying "Treba tvoje rozhodnutie" and offering no way to give one.
    """
    from backend.services.pipeline_status import ConsultationBlock, ConsultDecision, ConsultOption

    _version, state = _seed(db_session)
    both = "Zoznam faktúr — Špecifikácia: podľa dátumu | Obrazovka: podľa sumy"
    seen: dict[str, str] = {}

    async def _cards(*_a, **kw):
        seen["prompt"] = kw["prompt"]
        return PipelineStatusBlock(
            stage="vizual",
            kind="consultation",
            summary="Jeden rozpor.",
            awaiting="manazer",
            consultation=ConsultationBlock(
                id="c1",
                source=orchestrator._VIZUAL_CONFLICT_SOURCE,
                decisions=[
                    ConsultDecision(
                        key="zoradenie",
                        question="Podľa čoho sa má zoznam faktúr zoraďovať?",
                        options=[
                            ConsultOption(id="datum", label="Podľa dátumu", recommended=True),
                            ConsultOption(id="suma", label="Podľa sumy"),
                        ],
                    )
                ],
            ),
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _cards)
    out = await orchestrator._settle_vizual_conflict(db_session, state, [both])

    assert out.status == "blocked" and out.block_reason == "decision_needed"
    assert out.current_stage == "vizual", "the approval went through over an unresolved contradiction"
    # Both sides verbatim — a card that names the category instead of the cause walks him into the wrong
    # answer (ICCINT-41, ICCINT-43; both had to be fixed afterwards).
    assert both in seen["prompt"]
    # The card queue itself is written by the invoke path (stubbed out here) — what THIS function must get
    # right is that it asks for cards at all, which the next test pins down.
    assert "consultation.decisions[]" in seen["prompt"]


@pytest.mark.asyncio
async def test_the_conflict_takes_the_same_road_as_an_auditor_finding(db_session, monkeypatch) -> None:
    """It must go through the shared consultation path, not a bespoke ``decision_needed`` message.

    That path is what produces a queue ``DecisionCardsBar`` can render — and it carries the fail-open
    fallback and the re-consult cap for free. A hand-rolled block gets none of it and renders as a blocked
    build with nothing to click.
    """
    _version, state = _seed(db_session)
    both = "Zoznam faktúr — Špecifikácia: podľa dátumu | Obrazovka: podľa sumy"
    seen: dict[str, object] = {}

    async def _spy(_db, _state, *, source, verdict=None, **_kw):
        seen["source"] = source
        seen["findings"] = list(verdict.findings) if verdict else []
        return _state

    monkeypatch.setattr(orchestrator, "_settle_for_consultation", _spy)
    await orchestrator._settle_vizual_conflict(db_session, state, [both])

    assert seen["source"] == orchestrator._VIZUAL_CONFLICT_SOURCE
    assert seen["findings"] == [both], "the contradiction did not reach the card verbatim"


def test_the_card_says_where_the_contradiction_came_from(db_session) -> None:
    """Not "pri nezávislej previerke" — no review ran. Asserting a false provenance in the one message he
    decides from is the ICCINT-26 mistake repeated."""
    both = "Zoznam faktúr — Špecifikácia: podľa dátumu | Obrazovka: podľa sumy"
    vizual = orchestrator._consultation_directive(
        db_session, _uuid.uuid4(), source=orchestrator._VIZUAL_CONFLICT_SOURCE, findings=[both], proposed_fix=None
    )
    audit = orchestrator._consultation_directive(
        db_session, _uuid.uuid4(), source="auditor_upfront", findings=[both], proposed_fix=None
    )
    assert "obrazoviek s dokumentáciou" in vizual and "nezávislej previerke" not in vizual
    assert "nezávislej previerke" in audit, "the Auditor consultation changed wording"


# ── the narrowed review ───────────────────────────────────────────────────────


def test_the_review_can_be_narrowed_to_what_changed(db_session) -> None:
    version, _state = _seed(db_session)
    addition = "+ Zoznam faktúr sa zoraďuje podľa sumy zostupne."

    narrowed = orchestrator._auditor_upfront_directive(db_session, version.id, narrowed_to=addition)
    full = orchestrator._auditor_upfront_directive(db_session, version.id)

    assert addition in narrowed, "the Auditor was not given what it is supposed to judge"
    assert "ZÚŽENÁ PREVIERKA" in narrowed
    assert addition not in full and "ZÚŽENÁ PREVIERKA" not in full, "the ordinary review changed shape"


def test_nothing_added_means_no_second_review(db_session, tmp_path) -> None:
    """No documents changed → nothing to judge. Asking the Auditor about an empty diff is pure cost."""
    assert orchestrator._docs_changed_in_head(tmp_path) == ""


# ── the commit ────────────────────────────────────────────────────────────────


def test_the_documents_are_signed_together_with_the_screens(tmp_path) -> None:
    """One decision, one signature. Staging only ``frontend`` would leave the documents for a later commit."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "frontend" / "App.tsx").write_text("v1")
    (tmp_path / "docs" / "specification.md").write_text("# Špecifikácia\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")

    (tmp_path / "frontend" / "App.tsx").write_text("v2")  # the screens
    (tmp_path / "docs" / "specification.md").write_text("# Špecifikácia\nZoradenie podľa sumy.\n")  # the fold-back

    orchestrator._commit_vizual_changes(tmp_path)

    touched = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout
    assert "frontend/App.tsx" in touched
    assert "docs/specification.md" in touched, "the write-back landed outside the approved commit"


def test_a_project_without_a_docs_directory_still_gets_its_commit(tmp_path) -> None:
    """``git add`` fails the WHOLE call on one unmatched pathspec. Naming ``docs`` unconditionally would
    stage nothing where there is no docs directory — silently killing the squash the screens depend on."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "App.tsx").write_text("v1")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()

    (tmp_path / "frontend" / "App.tsx").write_text("v2")
    orchestrator._commit_vizual_changes(tmp_path)

    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert head != base, "the screens were never committed"
