"""DEV-105 — the repository tab shows an agent's planned red proof as a proof, not as a failure.

13.10.2026 the Director saw two red runs on NEX Inbox and asked what to do. One was a real failure; the other was
„proof(red): variant text copied into Settings.tsx (do not merge)“ on a side branch — the agent showing that the new
release-gate check catches a fault. Red was the right result, yet the tab said „Zlyhalo“ like for the real one. A proof
that unexpectedly PASSED is the finding — the guard caught nothing.

The mark is one for every project and the agent is told it where it is told about red proofs (the task brief's push rule
and the charter); the tab reads only that mark.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import ci_runs, orchestrator
from tests.test_the_repository_tab_shows_the_ci_runs import REPO, FakeGitHub, _run, github  # noqa: F401

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md"
PROOF = "proof(red): variant text copied into Settings.tsx (do not merge) (TASK #33.3.1)\n\nbody"


def test_a_red_proof_reads_as_planned():
    run = ci_runs.run_from_github(_run(28, conclusion="failure", branch="ci/v180-gate-red-sp20", message=PROOF))

    assert (run.state, run.state_label) == ("proof", "Dôkaz — červené podľa plánu")


def test_a_proof_that_passed_is_the_finding():
    run = ci_runs.run_from_github(_run(29, conclusion="success", branch="ci/v180-gate-red-sp20", message=PROOF))

    assert (run.state, run.state_label) == ("proof_passed", "Dôkaz prešiel — mal byť červený")


@pytest.mark.parametrize(
    "message",
    [
        "chore(1.8.0): memory — gate run with SP-20 (TASK #33.3.1)",
        "fix: a commit named proof(red): on the main branch is still a failure\n\nbody",
    ],
)
def test_every_other_red_run_is_a_failure(message):
    run = ci_runs.run_from_github(_run(30, conclusion="failure", message=message))

    assert (run.state, run.state_label) == ("failure", "Zlyhalo")


async def test_the_step_that_went_red_as_planned_is_named_too(github):  # noqa: F811
    github(FakeGitHub([_run(1, conclusion="failure", message=PROOF)]))

    listing = await ci_runs.listing(REPO, timeout=5)

    assert [r.failed_step for r in listing.runs] == ["gate › Brána vydania (release_smoke_test.sh)"]


def test_the_agent_is_told_the_mark_in_the_brief_and_in_the_charter():
    assert ci_runs.PROOF_MARK in orchestrator.PUSH_RULE
    assert ci_runs.PROOF_MARK in CHARTER.read_text(encoding="utf-8")
