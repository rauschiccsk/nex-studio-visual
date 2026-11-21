"""DEV-459 — the cockpit lets no app ask the customer for an Anthropic API key: ICC calls Claude through the Claude
subscription only (Knowledge Base ``icc/DECISIONS.md`` D-001).

21.11.2026 Orthodox Hermenia's Špecifikácia §17.3 asked for ``ANTHROPIC_API_KEY`` as a required customer secret and the
app called Claude with the ``anthropic`` library. The Príprava, the Návrh, its review and the deploy had no rule to
stop it; the yearly subscription code on the card was refused and nothing with AI worked in PROD. Now the approval of
such a Špecifikácia and the deploy of such an app are refused with a sentence, and both charters carry the rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import env_contract, orchestrator
from backend.services.orchestrator import OrchestratorError
from tests.test_conversation_kontrola import _make_version, _seed_priprava

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
API_KEY = "# @customer-secret required: „Kľúč API Anthropic pre AI modely aplikácie“\nANTHROPIC_API_KEY=\n"
SUBSCRIPTION = (
    "# @customer-secret required: „Ročný kód predplatného Claude (claude setup-token)“\nCLAUDE_CODE_OAUTH_TOKEN=\n"
)


def test_a_required_api_key_stops_the_deploy_with_a_sentence():
    blockers = env_contract.deploy_blockers(
        env_contract.parse(API_KEY), template_keys=["ANTHROPIC_API_KEY"], values={"ANTHROPIC_API_KEY": "x"}
    )

    assert blockers == [env_contract.API_KEY_REFUSED.format(keys="ANTHROPIC_API_KEY")]


@pytest.mark.parametrize(
    "text",
    [
        SUBSCRIPTION,
        # NEX HelpDesk: an optional key for an outside agent that brings its own — not the app's access to Claude.
        "# @customer-secret: „Kľúč Anthropic API — len pre agenta s vlastným kľúčom“\nANTHROPIC_API_KEY=\n",
    ],
)
def test_the_subscription_code_or_an_optional_key_does_not(text):
    contract = env_contract.parse(text)

    assert env_contract.api_key_demanded(contract) == []
    assert env_contract.deploy_blockers(contract, template_keys=[], values={"CLAUDE_CODE_OAUTH_TOKEN": "x"}) == []


def _priprava_with_spec(db_session, tmp_path, spec: str):
    version, _project = _make_version(db_session, source_path=str(tmp_path))
    rel = orchestrator._priprava_spec_rel(version.version_number)
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(spec, encoding="utf-8")
    _seed_priprava(db_session, version.id)
    return version


@pytest.mark.asyncio
async def test_a_specification_asking_for_an_api_key_is_not_approved(db_session, tmp_path):
    version = _priprava_with_spec(
        db_session, tmp_path, "## 17.3 Prístup ku Claudovi\n\n- Kľúč API inštalácie:\n\n  ```\n  " + API_KEY + "  ```\n"
    )

    with pytest.raises(OrchestratorError) as refused:
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec", payload={})

    assert str(refused.value) == orchestrator.SPEC_ASKS_API_KEY.format(
        keys="ANTHROPIC_API_KEY", button=orchestrator._press("uprav")
    )


@pytest.mark.asyncio
async def test_the_design_document_is_read_too_and_the_subscription_passes(db_session, tmp_path):
    version = _priprava_with_spec(db_session, tmp_path, "## Prístup ku Claudovi\n\n" + SUBSCRIPTION)
    design = tmp_path / orchestrator._priprava_spec_rel(version.version_number)
    design.with_name("design.md").write_text("| premenná |\n\n" + API_KEY, encoding="utf-8")

    with pytest.raises(OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec", payload={})

    design.with_name("design.md").unlink()
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec", payload={})
    assert state.next_action.startswith("Špecifikácia schválená")


def test_both_charters_carry_the_rule():
    builder = " ".join((TEMPLATES / "ai-agent-charter.md").read_text(encoding="utf-8").split())
    auditor = " ".join((TEMPLATES / "auditor-charter.md").read_text(encoding="utf-8").split())

    assert "**Claude len cez predplatné (DEV-459).** Aplikácia volá Clauda výhradne cez predplatné Claude" in builder
    assert "`nex_app_kit.claude`" in builder and "ANTHROPIC_API_KEY` nenavrhuj" in builder
    assert "**Claude len cez predplatné (DEV-459)**" in auditor and "je blokujúci nález" in auditor
