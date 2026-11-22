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

DEV-376 — the cockpit's own sentences name the buttons from here too. 14.11.2026: 55 places of the backend wrote a
button's name into a sentence by hand — „Manažér: posúdiť výsledok Programovania (Schváliť / Uprav).“ while the screen
drew „Prejsť na overenie“ and „Upraviť“, „pokračuj cez 'Pokračovať'“ next to „Pokračovať v stavbe“. Director, on all
the buttons in all 55 places: „Súhlasím“. A sentence names a button by its action (:func:`press`) and the name is
filled in as the screen draws it: the state's sentence when it is read — always for the state it is shown with
(:func:`read_state`, :func:`state_sentence`) — and a message or a refused action's reason when it is written, because
it goes to the Manažér at once (:func:`say`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineState
from backend.services import fast_fix

if TYPE_CHECKING:
    from backend.schemas.pipeline import PipelineStateRead

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
    # No action of the engine — the live preview's own button on the Vizuál's strip (DEV-127, its own route).
    "restart_preview": "Spustiť náhľad znova",
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
    # Imported here: the orchestrator's own sentences use :func:`press` while it is being imported.
    from backend.services import orchestrator

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


# ─── DEV-376: a button named in a sentence ─────────────────────────────────────────────────────────────────────────

#: How a sentence carries a button until its name is filled in — characters no sentence of the cockpit uses.
_PRESSED = re.compile(r"⟦([a-z_]+)⟧")


def press(action: str) -> str:
    """The button of ``action`` named in a sentence — by its action, never by its name; the name is filled in as the
    screen draws it (:func:`name_buttons`). An action without a button of its own is refused at once."""
    if action not in BASE:
        raise ValueError(f"no build button carries the action {action!r}")
    return f"⟦{action}⟧"


def names_a_button(text: Optional[str]) -> bool:
    return bool(text) and _PRESSED.search(text) is not None


def name_buttons(text: str, labels: dict[str, str]) -> str:
    """``text`` with every button it names written as these labels name it, in „…“. A button the state draws under no
    name (``uprav`` in the Vizuál, where the conversation box carries the change) keeps its plain name."""
    return _PRESSED.sub(lambda m: f"„{labels.get(m.group(1)) or BASE.get(m.group(1), m.group(1))}“", text)


def state_labels(db: Session, state: PipelineState) -> dict[str, str]:
    """The names the board hands out with this state — worked out on the board's own final set of offered actions.
    The state alone is not enough: at the plan stop the board holds „Prejsť na overenie“ back (tasks remain), so the
    edit button is the conversation box's „Uprav“, not the approve bar's „Upraviť“."""
    from backend.api.routes.pipeline import _board

    return _board(db, state.version_id, limit=0).action_labels


def state_sentence(db: Session, state: PipelineState, labels: Optional[dict[str, str]] = None) -> str:
    """The state's sentence (``next_action``) with its buttons named for the state it is shown with — the one way
    the sentence leaves the cockpit (the board, a live frame, the Dedo door, Poradca)."""
    text = state.next_action or ""
    if not names_a_button(text):
        return text
    return name_buttons(text, labels if labels is not None else state_labels(db, state))


def read_state(db: Session, state: PipelineState, labels: Optional[dict[str, str]] = None) -> PipelineStateRead:
    """The state as it goes out — its sentence's buttons named (:func:`state_sentence`). The board passes the labels
    it hands out with it, so the sentence and the buttons read the same words."""
    from backend.schemas.pipeline import PipelineStateRead

    read = PipelineStateRead.model_validate(state)
    refused = fast_fix.cancel_refusal(state)
    return read.model_copy(
        update={
            "next_action": state_sentence(db, state, labels),
            "cancellable": refused is None,
            "cancel_refusal": refused,
        }
    )


def say(db: Session, state: Optional[PipelineState], text: str) -> str:
    """A text that reaches the Manažér at once — a message, a refused action's reason — with its buttons named as the
    screen draws them in the state the build stands in now."""
    if not names_a_button(text):
        return text
    return name_buttons(text, state_labels(db, state) if state is not None else BASE)
