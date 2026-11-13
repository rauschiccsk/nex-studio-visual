"""DEV-373 — the name of every build button, in one place: what the screen draws is what Poradca says.

13.11.2026, NEX Legal Watch 0.1.0 after its Verifikácia: Poradca told the Manažér to press „Schváliť“; the screen
drew „Schváliť na Hotovo“. The names lived twice — the screen's bars wrote their own texts (the same ``schvalit``
reads differently in each phase), Poradca kept a hand-made copy of them, and its test checked only that every
action was in the copy, not that the words matched. Nine names had drifted apart. Director, on naming the buttons
in one place: „Áno oprav.“

Here the cockpit works out, for the state the build is in, the name of every button of the build; the board hands
them out (``PipelineBoardRead.action_labels``), the screen draws them and Poradca reads the same words. The plain
names (no state needed) are also written into the frontend as ``actionLabels.generated.json`` by ``npm run
codegen`` — the drift gate keeps that file equal to :data:`BASE`.

Actions without a button of their own are not here: ``decide`` is a choice on a Decision Card (the card names its
options and its own button), ``ask`` and ``verdict`` belong to the agents.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineState
from backend.services import orchestrator

#: The name each button carries when nothing in the state changes it.
BASE: dict[str, str] = {
    "start": "Spustiť tvorbu špecifikácie",
    "approve_spec": "Schváliť Špecifikáciu",
    "zostav_plan": "Zostaviť plán",
    "spustit_stavbu": "Spustiť stavbu",
    "spustit_vizual": "Spustiť vizuál",
    "schvalit": "Schváliť plán",
    "uprav": "Upraviť",
    "pokracovat": "Pokračovať v stavbe",
    "answer": "Odpovedať",
    "schvalit_schemu": "Schváliť štruktúru databázy",
    "pause": "Pozastaviť",
    "overit_znovu": "Over znova",
    "overit_bez_opravy": "Znova overiť bez opravy",
    "opravit_podla_ci": "Vrátiť Staviteľovi na opravu",
    "skontrolovat": "Skontrolovať",
    "hotovo": "Označiť ako hotové",
    "na_riadnu_verziu": "Preniesť do riadnej verzie",
    "nahlasit_znova": "Nahlásiť znova",
    "zopakovat_konzultaciu": "Skúsiť konzultáciu znova",
    "vratit_do_pripravy": "Vrátiť do Prípravy",
}

#: Actions of the vocabulary that are no button of their own (see the module docstring).
NOT_BUTTONS: frozenset[str] = frozenset({"decide", "ask", "verdict"})

#: The same ``schvalit`` advances a different gate in each phase, and the button says which.
APPROVE_BY_STAGE: dict[str, str] = {
    "vizual": "Schváliť vizuál",
    "programovanie": "Prejsť na overenie",
    "verifikacia": "Schváliť na Hotovo",
}
#: ``uprav`` is drawn by whichever control carries it, each with its own word: the bar of an error or a negative check
#: offers a retry, the approve bar „Upraviť“ beside the approval, the conversation box „Uprav“ beside „Poslať“.
RETRY_LABEL = "Skús znova"
RETRY_REASONS: frozenset[str] = frozenset({"agent_error", "system_error", "parse_exhaustion", "check_failed"})
COMPOSER_REVISE_LABEL = "Uprav"
#: A question of the agent's: its bar takes the answer only and the conversation box folds away — no ``uprav``.
ANSWER_ONLY_REASONS: frozenset[str] = frozenset({"agent_question", "schema_approval"})
#: ``pokracovat`` after our technical team's fix, after a restart or with a fix ready — its own bar, one word.
CONTINUE_LABEL = "Pokračovať"
#: ``pokracovat`` at the stop after the task plan STARTS the coding (DEV-214).
START_CODING_LABEL = "Spustiť programovanie"
#: ``overit_bez_opravy`` when a Verifikácia run stalled (not a fix loop): it runs the verification again.
RERUN_VERIFICATION_LABEL = "Znova spustiť overenie"


def _released(state: PipelineState) -> bool:
    return bool(
        state.resume_after_framework_fix
        or state.resume_after_restart
        or (state.status == "paused" and state.pause_reason == "fix_ready")
    )


def _revise_label(state: PipelineState, actions: list[str]) -> Optional[str]:
    """The word ``uprav`` carries on screen in this state — ``None`` where no control draws it (mirrors the screen's
    ``composerOffersRevise`` / ``approveBarCarriesRevise`` and the block bar's ``blockRecoveryOwnsInput``)."""
    if state.status == "blocked" and state.block_reason in RETRY_REASONS:
        return RETRY_LABEL
    if state.status == "blocked" and state.block_reason in ANSWER_ONLY_REASONS:
        return None
    if state.current_stage == "vizual":
        return None  # the conversation box is the Vizuál's change channel; neither bar draws „uprav“ there
    return BASE["uprav"] if "schvalit" in actions else COMPOSER_REVISE_LABEL


def action_labels(db: Session, state: Optional[PipelineState], actions: list[str]) -> dict[str, str]:
    """The name of every build button the screen can draw in this state — also of one not offered right now (the CI
    gate draws the step it holds back, greyed). ``actions`` are the board's offered actions, final."""
    labels = dict(BASE)
    if state is None:
        return labels
    labels["schvalit"] = APPROVE_BY_STAGE.get(state.current_stage, BASE["schvalit"])
    revise = _revise_label(state, actions)
    if revise is None:
        del labels["uprav"]
    else:
        labels["uprav"] = revise
    if _released(state):
        labels["pokracovat"] = CONTINUE_LABEL
    elif orchestrator.plan_awaits_start(db, state.version_id):
        labels["pokracovat"] = START_CODING_LABEL
    if state.current_stage == "verifikacia" and state.block_reason != "decision_needed":
        labels["overit_bez_opravy"] = RERUN_VERIFICATION_LABEL
    return labels
