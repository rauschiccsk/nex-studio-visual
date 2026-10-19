"""DEV-146 — a failed CI run that a newer run of the same workflow on the same commit overtook no longer says red.

19.10.2026, moving the CI runners to a Docker of their own (DEV-145): the first runs of NEX Websites (39288215037) and
Career Asistent (39288221043) failed because of the runner, not the code. Newer runs of the same workflow on the same
commits passed (39294358070, 39294362606), yet the cockpit kept saying "CI zlyhalo" and blocked deploying NEX Websites
v1.0.0 — ``verdikt`` took ANY finished failed run of the commit as the answer. It went away only by re-running the
failed runs on GitHub, a step outside the cockpit the Manažér does not know.

Pinned here: what counts is the newest run of each workflow on each ref (a version tag push is its own ref, DEV-51);
red still wins over runs of other workflows still going; the screen's link, Poradca and the gate's waiting count the
same runs as the verdict.
"""

from __future__ import annotations

import json

import pytest

from backend.services import ci_status


def _run(workflow: str, status: str, conclusion: str | None, number: int, ref: str = "main") -> dict:
    return {
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "databaseId": number,
        "headBranch": ref,
        "url": f"https://github.com/o/r/actions/runs/{number}",
    }


def _both_orders(rows: list[dict]) -> list[list[dict]]:
    return [rows, list(reversed(rows))]


def test_the_case_of_19_10_is_green() -> None:
    """NEX Websites 43b60e0 as GitHub listed it on 19.10.2026."""
    rows = [
        _run("CI", "completed", "success", 38756204861),
        _run("CI", "completed", "failure", 39288215037),
        _run("CI", "completed", "success", 39294358070),
    ]
    for order in _both_orders(rows):
        state, detail = ci_status.verdikt(order)

        assert detail == (
            "CI zelené (postup CI, beh 39294358070; starší neúspešný beh 39288215037 prekonal novší beh 39294358070)"
        )
        assert state == "green"
        assert ci_status.prvy_zlyhany(order) is None, "the screen would still link the overtaken failure"


def test_the_green_sentence_says_which_newer_run_overtook_the_failure() -> None:
    rows = [_run("CI", "completed", "failure", 10), _run("CI", "completed", "success", 11)]

    _, detail = ci_status.verdikt(rows)

    assert detail == "CI zelené (postup CI, beh 11; starší neúspešný beh 10 prekonal novší beh 11)"


def test_a_newer_failure_after_an_older_green_is_red() -> None:
    rows = [_run("CI", "completed", "success", 10), _run("CI", "completed", "failure", 11)]
    for order in _both_orders(rows):
        state, detail = ci_status.verdikt(order)

        assert state == "red", detail
        assert "beh 11" in detail and ci_status.prvy_zlyhany(order)["databaseId"] == 11


def test_a_failure_of_one_workflow_is_not_overtaken_by_another_workflow() -> None:
    rows = [_run("CI", "completed", "failure", 10), _run("Release smoke gate", "completed", "success", 11)]
    for order in _both_orders(rows):
        assert ci_status.verdikt(order)[0] == "red"


def test_a_tag_push_is_its_own_ref() -> None:
    """DEV-51: the run a version tag starts answers for the tag, not for the branch — and the other way round."""
    branch_failed = [
        _run("Release smoke gate", "completed", "failure", 10),
        _run("Release smoke gate", "completed", "success", 11, ref="v1.0.0"),
    ]
    tag_failed = [
        _run("Release smoke gate", "completed", "success", 10),
        _run("Release smoke gate", "completed", "failure", 11, ref="v1.0.0"),
    ]
    for rows in (branch_failed, tag_failed):
        for order in _both_orders(rows):
            assert ci_status.verdikt(order)[0] == "red", order


def test_a_cancelled_newer_run_does_not_overtake_a_failure() -> None:
    """Cancelling a re-run says nothing about the code — the failure stands and still blocks."""
    rows = [_run("CI", "completed", "failure", 10), _run("CI", "completed", "cancelled", 11)]
    for order in _both_orders(rows):
        state, detail = ci_status.verdikt(order)

        assert state == "red", detail
        assert ci_status.prvy_zlyhany(order)["databaseId"] == 10


def test_a_cancelled_newer_run_does_not_hide_an_older_green() -> None:
    rows = [_run("CI", "completed", "success", 10), _run("CI", "completed", "cancelled", 11)]
    for order in _both_orders(rows):
        assert ci_status.verdikt(order) == ("green", "CI zelené (postup CI, beh 10)")


def test_runs_without_a_workflow_name_never_overtake_each_other() -> None:
    """Without the name the runs cannot be matched to one workflow — each one still counts."""
    rows = [_run("", "completed", "failure", 10), _run("", "completed", "success", 11)]
    for order in _both_orders(rows):
        assert ci_status.verdikt(order)[0] == "red"


def test_a_newer_run_still_going_waits_instead_of_the_older_failure() -> None:
    """Re-running a failed workflow: until the new run finishes, the commit is not known — neither green nor red."""
    rows = [_run("CI", "completed", "failure", 10), _run("CI", "in_progress", None, 11)]
    for order in _both_orders(rows):
        state, detail = ci_status.verdikt(order)

        assert state == "unknown", detail
        assert [r["databaseId"] for r in ci_status.bezi(order)] == [11]
        assert ci_status.prvy_zlyhany(order) is None


def test_red_still_wins_over_another_workflow_still_going() -> None:
    """Why red has priority stays: waiting for siblings must not let a red decay into a non-blocking ``unknown``."""
    rows = [_run("CI", "completed", "failure", 10), _run("Smoke", "in_progress", None, 11)]

    assert ci_status.verdikt(rows)[0] == "red"


def test_an_overtaken_run_stuck_running_is_not_waited_for() -> None:
    rows = [_run("CI", "in_progress", None, 10), _run("CI", "completed", "success", 11)]
    for order in _both_orders(rows):
        assert ci_status.bezi(order) == []
        assert ci_status.verdikt(order)[0] == "green"


@pytest.mark.asyncio
async def test_the_gate_does_not_wait_for_an_overtaken_run(monkeypatch, tmp_path) -> None:
    """The gate counts what is still going with the same rule — an older run left running does not hold it up."""
    from backend.services import orchestrator

    rows = [_run("CI", "in_progress", None, 10), _run("CI", "completed", "success", 11)]

    async def step(command, _timeout):
        if "remote.origin.url" in command:
            return 0, "https://github.com/rauschiccsk/nex-inbox.git"
        return 0, json.dumps(rows)

    async def no_waiting(_seconds):
        pytest.fail("the gate waited for a run that a newer run of its workflow overtook")

    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "abc1234")
    monkeypatch.setattr(orchestrator, "_project_has_ci", lambda _root: True)
    monkeypatch.setattr(orchestrator, "_run_publish_step", step)
    monkeypatch.setattr(orchestrator.asyncio, "sleep", no_waiting)

    state, detail = await orchestrator._ci_status_for_head(tmp_path)

    assert state == "green", detail
