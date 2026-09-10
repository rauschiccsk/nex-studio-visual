"""Stratená niť rozhovoru nezablokuje vydanie (ICCINT-110).

**Čo sa stalo.** NEX Manager 1.2.0 uviazol vo Verifikácii dvakrát po sebe. Zápis z Audítorovho behu:

    claude exited with code 1
    --- stderr ---
    No conversation found with session ID: 5eb01d07-…

Engine sa pokúsil nadviazať na sedenie, ktoré už neexistovalo. Agentove sedenia žijú v kontajneri
(``HOME=/root``, a ``/root/.claude`` sa pri nasadení vytvára nanovo), takže ich **zoberie každý
reštart** — a ja som v ten deň nasadzoval päťkrát, kým Director staval.

**Prečo je zomrieť nesprávna odpoveď.** Chýbajúca história rozhovoru nie je chyba appky ani nález
Audítora. Prompt ťahu je sebestačný: engine v ňom Audítorovi hovorí, čo má overiť. Zablokovať kvôli
tomu bránu vydania znamená zastaviť dobrú verziu z dôvodu, ktorý s ňou nesúvisí.

**Čo Manažér videl namiesto toho:** „Verdikt Auditora sa nepodarilo spracovať ani po opakovaných
pokusoch.“ To posiela človeka hľadať chybu v Audítorovi — agent sa pritom vôbec nerozbehol.

⚠️ Opakovať to isté volanie nepomôže: sedenie sa už neobjaví. Preto sa skúsi **raz** znova, s čerstvým
sedením — a **nahlas**, aby sa nikto nedozvedel až z výsledku, že agent pokračoval bez pamäte.
"""

from __future__ import annotations

import inspect
import re
from uuid import uuid4

import pytest

from backend.services import claude_agent
from backend.services.claude_agent import build_claude_argv

STRATENE = "claude exited with code 1: No conversation found with session ID: 5eb01d07-316c-48f1"


def test_the_lost_session_message_is_recognised() -> None:
    """⚠️ Presne tá veta, ktorá dvakrát zablokovala vydanie — prekopírovaná zo záznamu behu."""
    assert claude_agent._SESSION_GONE_RE.search(STRATENE)


@pytest.mark.parametrize(
    "iny",
    [
        "claude exited with code 1: 529 overloaded",
        "claude exited with code 125: image not found",
        "claude exited with code 1: rate limit exceeded",
    ],
)
def test_other_failures_are_not_mistaken_for_a_lost_session(iny: str) -> None:
    """Bez tohto tvrdenia by sa čerstvé sedenie zakladalo pri KAŽDOM páde — a tým by sa ticho
    zahadzovala história rozhovoru aj tam, kde je v poriadku."""
    assert not claude_agent._SESSION_GONE_RE.search(iny)


def test_a_normal_turn_still_resumes() -> None:
    """Bežný ťah musí na rozhovor nadväzovať — inak by agent zakaždým začínal bez pamäte."""
    args = build_claude_argv(streaming=False, claude_session_id=uuid4(), prompt="x", charter_text=None)

    assert "--resume" in args and "--session-id" not in args


def test_after_a_lost_session_the_turn_starts_fresh() -> None:
    """⚠️ Jadro opravy: keď sa nadväzovať nie je na čo, ťah sedenie ZALOŽÍ."""
    sid = uuid4()
    args = build_claude_argv(
        streaming=False, claude_session_id=sid, prompt="x", charter_text=None, force_new_session=True
    )

    assert "--resume" not in args, "ťah sa stále pokúša nadviazať na sedenie, ktoré neexistuje"
    assert "--session-id" in args and str(sid) in args


def test_a_first_turn_with_a_charter_is_untouched() -> None:
    """Prvý ťah fázy sedenie zakladá aj dnes — oprava sa ho nesmie dotknúť."""
    args = build_claude_argv(streaming=False, claude_session_id=uuid4(), prompt="x", charter_text="PRAVIDLÁ")

    assert "--session-id" in args and "--append-system-prompt" in args and "--resume" not in args


def test_the_retry_happens_once_and_says_so_out_loud() -> None:
    """⚠️ Dve veci naraz, obe merané na ZDROJI (skutočný beh by chcel bežiaceho agenta):

    1. opakuje sa **raz** — ``zacni_nacisto`` sa nastaví a druhýkrát už podmienka neplatí, takže sa
       nemôže vzniknúť slučka;
    2. **povie sa to nahlas** — bez zápisu by sa nikto nedozvedel, že agent pokračoval bez pamäte.
    """
    zdroj = inspect.getsource(claude_agent.invoke_claude)

    assert "not zacni_nacisto and _SESSION_GONE_RE.search" in zdroj, "opakovanie nie je ohraničené na jeden pokus"
    assert re.search(r"logger\.warning\(\s*\n?\s*\"claude session", zdroj), "strata pamäte sa nikde nehlási"


def test_the_flag_reaches_the_argv_builder() -> None:
    """⚠️ Spoj, nie okolie spoja.

    Tvrdenia vyššie volajú ``build_claude_argv`` priamo. Keby ``_invoke_once`` príznak ďalej
    neodovzdal, ťah by sa naďalej pokúšal nadväzovať — a stráže by boli zelené. Ten istý tvar chyby
    som v ten deň mal už dvakrát.
    """
    assert "force_new_session" in inspect.signature(claude_agent._invoke_once).parameters
    assert "force_new_session=force_new_session" in inspect.getsource(claude_agent._invoke_once)


def test_the_release_gate_still_refuses_a_missing_verdict() -> None:
    """Čo sa NESMIE zmeniť: keď Audítor verdikt naozaj nedá, brána vydania drží.

    Oprava rieši stratené sedenie, nie chýbajúci verdikt. Keby sa pri tom uvoľnila brána, vymenili by
    sme zablokované vydanie za vydanie neoverené — a to je horšie.
    """
    from backend.services import orchestrator

    zdroj = inspect.getsource(orchestrator)

    assert "release gate, fail-closed" in zdroj
