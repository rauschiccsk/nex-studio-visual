"""DEV-35 — the Auditor's notes from a PASSED design review reach the AI Agent when the task plan is built.

Found by the Director 09.10.2026 on NEX Inbox 1.7.0. The fifth design review passed and the Auditor wrote:
„ostávajú štyri neblokujúce poznámky, ktoré AI Agent opraví pri programovaní" — two of them about false
messages the staff would get during an outage. The Director approved the design on that promise. Nothing in
the cockpit kept it: a FAILED review turns its findings into Decision Cards, a PASSED one only stores them in
its message, and the plan passes at the start of Programovanie were handed ``directive=None``.

Pinned here:
* the skeleton pass carries every note of every passed review (deduplicated), never a note from a failed one
  (those went to the cards);
* the skeleton must say, for EACH note, which feat covers it (or why it no longer applies) — a skeleton that
  misses one, points at a feat it does not have, or invents a note number is sent back;
* the feat that covers a note gets it in its own task pass;
* the Manažér sees every note and where the plan put it.
"""

import uuid

from sqlalchemy import select

from backend.db.models.tasks import Task
from backend.services import orchestrator
from tests.test_orchestrator_v2_programovanie import (
    _done_block,
    _epics,
    _make_version,
    _no_baseline_git,
    _plan_gate_reports,
    _seed_programovanie,
    _stub_mech,
    _stub_turns,
)

NOTE_RESTART = "Uzatvorenie hlásenia treba naprogramovať podľa pravidla, nie podľa náčrtu — po reštarte appky."
NOTE_ONE_OUTAGE = "Výpadok má ostať jeden aj vtedy, keď sa počas neho zmení jeho povaha."
NOTE_TABLE = "Treba opraviť riadok v tabuľke hraničných prípadov, ktorý pre plný disk sľubuje jeden e-mail."
FAILED_FINDING = "Jedna problémová faktúra môže natrvalo zastaviť doručovanie všetkých ostatných."

PLAN = {"Foundation": ["Doručovateľ", "Upozornenia"]}
FEAT_TASKS = {
    "Doručovateľ": [{"title": "slučka doručovateľa", "task_type": "backend"}],
    "Upozornenia": [{"title": "texty e-mailov", "task_type": "docs"}],
}


def _verdict(db, version_id, findings, *, hole: bool) -> None:
    """One upfront design review exactly as the engine records it: the Auditor's verdict, and — only when it
    found a hole — the engine's ``upfront_review_hole`` note right after it (that note is what sends the
    findings to a consultation)."""
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="navrh",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="Previerka Návrhu.",
        payload={"findings": findings, "upfront_review": True},
    )
    if hole:
        orchestrator._record_message(
            db,
            version_id=version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content="Auditor našiel medzeru — spúšťa sa konzultácia.",
            payload={"phase": "navrh", "upfront_review_hole": True},
        )
    db.flush()


def _note(text):
    return {"text": text, "blocking": False}


def _skeleton(review_notes):
    obj = {
        "epics": [{"title": e, "feats": [{"title": f} for f in feats]} for e, feats in PLAN.items()],
        "cross_cutting_rules": "## Invarianty\n- jeden výpadok",
        "flagship_features": ["Faktúra sa doručí do Genesisu"],
    }
    if review_notes is not None:
        obj["review_notes"] = review_notes
    return obj


def _stub_plan(monkeypatch, skeletons):
    """Drive the plan passes: skeleton answers come from ``skeletons`` in order (the last one repeats); a feat
    pass answers with that feat's tasks. Every prompt is captured, tagged with the pass it belongs to."""
    prompts: list[tuple[str, str]] = []
    queue = list(skeletons)

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt or "nepodarilo spracovať" in prompt:
            n = sum(1 for kind, _ in prompts if kind == "skeleton")
            prompts.append(("skeleton", prompt))
            return ("", None, queue[min(n, len(queue) - 1)])
        for title, tasks in FEAT_TASKS.items():
            if f"Pre funkciu „{title}“" in prompt:
                prompts.append((title, prompt))
                return ("", None, {"tasks": tasks})
        raise AssertionError(f"unexpected plan-pass prompt: {prompt[:120]}")

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))
    return prompts


def _build(db_session, monkeypatch, tmp_path, *, reviews, skeletons):
    version, _ = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    for findings, hole in reviews:
        _verdict(db_session, version.id, findings, hole=hole)
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
    prompts = _stub_plan(monkeypatch, skeletons)
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    return version, state, prompts


COVERED = [
    {"note": 1, "feat": "Doručovateľ", "resolution": "Ukončenie výpadku sa naprogramuje podľa pravidla."},
    {"note": 2, "feat": "Doručovateľ", "resolution": "Zmena druhu výpadku nezačne nový výpadok."},
    {"note": 3, "feat": "Upozornenia", "resolution": "Riadok o plnom disku sa opraví v Špecifikácii."},
]

# A failed review (its findings went to the cards), then the passed Návrh review, then the narrowed review at
# Vizuál approval — which repeats one note and adds one.
REVIEWS = [
    ([{"text": FAILED_FINDING, "blocking": True}, _note("Drobnosť z neúspešného kola.")], True),
    ([_note(NOTE_RESTART), _note(NOTE_ONE_OUTAGE)], False),
    ([_note(NOTE_ONE_OUTAGE), _note(NOTE_TABLE)], False),
]


async def test_the_notes_of_passed_reviews_are_in_the_skeleton_prompt_and_nothing_else(
    db_session, monkeypatch, tmp_path
):
    version, state, prompts = _build(db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[_skeleton(COVERED)])

    await orchestrator._run_build_round(db_session, state)

    skeleton_prompt = next(p for kind, p in prompts if kind == "skeleton")
    assert f"1. {NOTE_RESTART}" in skeleton_prompt
    assert f"2. {NOTE_ONE_OUTAGE}" in skeleton_prompt
    assert f"3. {NOTE_TABLE}" in skeleton_prompt
    assert skeleton_prompt.count(NOTE_ONE_OUTAGE) == 1  # the repeated note is asked for once
    # A failed review's findings went to the Decision Cards — they are not handed over a second time.
    assert FAILED_FINDING not in skeleton_prompt
    assert "Drobnosť z neúspešného kola." not in skeleton_prompt
    assert "`review_notes`" in skeleton_prompt


async def test_the_feat_that_covers_a_note_gets_it_in_its_task_pass(db_session, monkeypatch, tmp_path):
    version, state, prompts = _build(db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[_skeleton(COVERED)])

    await orchestrator._run_build_round(db_session, state)

    feat_prompts = dict((kind, p) for kind, p in prompts if kind != "skeleton")
    assert NOTE_RESTART in feat_prompts["Doručovateľ"] and NOTE_ONE_OUTAGE in feat_prompts["Doručovateľ"]
    assert NOTE_TABLE not in feat_prompts["Doručovateľ"]
    assert NOTE_TABLE in feat_prompts["Upozornenia"] and NOTE_RESTART not in feat_prompts["Upozornenia"]


async def test_the_manager_sees_every_note_and_where_the_plan_put_it(db_session, monkeypatch, tmp_path):
    covered = [*COVERED[:2], {"note": 3, "feat": "", "resolution": "Riadok už opravil Vizuál v §9 Špecifikácie."}]
    version, state, _ = _build(db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[_skeleton(covered)])

    await orchestrator._run_build_round(db_session, state)

    plan_msg = _plan_gate_reports(db_session, version.id)[-1]
    assert plan_msg.payload["review_notes"] == [
        {"note": 1, "text": NOTE_RESTART, "feat": "Doručovateľ", "resolution": COVERED[0]["resolution"]},
        {"note": 2, "text": NOTE_ONE_OUTAGE, "feat": "Doručovateľ", "resolution": COVERED[1]["resolution"]},
        {"note": 3, "text": NOTE_TABLE, "feat": "", "resolution": "Riadok už opravil Vizuál v §9 Špecifikácie."},
    ]
    report = plan_msg.payload["report"]
    assert f"1. {NOTE_RESTART}\n   → funkcia „Doručovateľ“: {COVERED[0]['resolution']}" in report
    assert f"3. {NOTE_TABLE}\n   → už neplatí: Riadok už opravil Vizuál v §9 Špecifikácie." in report


async def test_a_skeleton_that_leaves_out_a_note_is_sent_back(db_session, monkeypatch, tmp_path):
    first = _skeleton([COVERED[0], COVERED[2]])  # note 2 missing
    version, state, prompts = _build(
        db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[first, _skeleton(COVERED)]
    )

    await orchestrator._run_build_round(db_session, state)

    skeleton_prompts = [p for kind, p in prompts if kind == "skeleton"]
    assert len(skeleton_prompts) == 2
    assert "chýba poznámka Audítora č. 2" in skeleton_prompts[1]
    assert [n["note"] for n in _plan_gate_reports(db_session, version.id)[-1].payload["review_notes"]] == [1, 2, 3]


async def test_a_note_pointing_at_a_feat_the_skeleton_does_not_have_is_sent_back(db_session, monkeypatch, tmp_path):
    wrong = [COVERED[0], {**COVERED[1], "feat": "Výpadky"}, COVERED[2]]
    version, state, prompts = _build(
        db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[_skeleton(wrong), _skeleton(COVERED)]
    )

    await orchestrator._run_build_round(db_session, state)

    retry = [p for kind, p in prompts if kind == "skeleton"][1]
    assert "poznámka Audítora č. 2 ukazuje na funkciu „Výpadky“, ktorá v kostre nie je" in retry


async def test_a_note_number_that_was_never_given_is_sent_back(db_session, monkeypatch, tmp_path):
    invented = [*COVERED, {"note": 4, "feat": "Upozornenia", "resolution": "Niečo navyše."}]
    version, state, prompts = _build(
        db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[_skeleton(invented), _skeleton(COVERED)]
    )

    await orchestrator._run_build_round(db_session, state)

    retry = [p for kind, p in prompts if kind == "skeleton"][1]
    assert "poznámka Audítora č. 4 neexistuje" in retry


async def test_a_skeleton_that_never_covers_the_notes_stops_the_build_and_writes_nothing(
    db_session, monkeypatch, tmp_path
):
    version, state, prompts = _build(db_session, monkeypatch, tmp_path, reviews=REVIEWS, skeletons=[_skeleton(None)])

    settled = await orchestrator._run_build_round(db_session, state)

    assert settled.status == "blocked"
    assert _epics(db_session, version.id) == []
    assert db_session.execute(select(Task)).scalars().all() == []


async def test_without_a_passed_review_the_plan_is_built_as_before(db_session, monkeypatch, tmp_path):
    reviews = [([{"text": FAILED_FINDING, "blocking": True}], True)]
    version, state, prompts = _build(db_session, monkeypatch, tmp_path, reviews=reviews, skeletons=[_skeleton(None)])

    settled = await orchestrator._run_build_round(db_session, state)

    assert settled.status == "awaiting_manazer"
    skeleton_prompt = next(p for kind, p in prompts if kind == "skeleton")
    assert "`review_notes`" not in skeleton_prompt and FAILED_FINDING not in skeleton_prompt
    plan_msg = _plan_gate_reports(db_session, version.id)[-1]
    assert "review_notes" not in plan_msg.payload and "report" not in plan_msg.payload


async def test_a_note_of_several_lines_stays_under_its_number(db_session, monkeypatch, tmp_path):
    # NEX Inbox 1.7.0, note 4: „Drobnosti v textoch a tabuľkách:" followed by its own „- …" lines. Left at the
    # margin, those lines end the numbered list in the Manažér's view and the arrow lands under nothing.
    several = (
        "Drobnosti v textoch a tabuľkách:\n- veta neutrálneho textu nemusí byť pravdivá;\n- číslovanie poistiek nesedí."
    )
    reviews = [([_note(several)], False)]
    answer = [{"note": 1, "feat": "Upozornenia", "resolution": "Texty sa opravia."}]
    version, state, prompts = _build(db_session, monkeypatch, tmp_path, reviews=reviews, skeletons=[_skeleton(answer)])

    await orchestrator._run_build_round(db_session, state)

    nested = (
        "1. Drobnosti v textoch a tabuľkách:\n   - veta neutrálneho textu nemusí byť pravdivá;\n"
        "   - číslovanie poistiek nesedí."
    )
    assert (
        f"{nested}\n   → funkcia „Upozornenia“: Texty sa opravia."
        in (_plan_gate_reports(db_session, version.id)[-1].payload["report"])
    )
    assert nested in next(p for kind, p in prompts if kind == "skeleton")
    assert nested in dict(prompts)["Upozornenia"]
