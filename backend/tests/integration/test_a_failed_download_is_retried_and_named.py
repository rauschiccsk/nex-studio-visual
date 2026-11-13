"""DEV-371 — a failed start check keeps its real cause, and a failed download is retried once and said plainly.

13.11.2026, NEX Legal Watch 0.1.0: after the fix round the start check failed while building the image — ``pip
install`` of the dependencies ended with exit 1 — and the build stood two days. The cockpit kept only the last 400
characters of the compose output: „target migrate: failed to solve … did not complete successfully: exit code: 1“.
Why pip failed was cut off; nobody could tell a failed download from a broken dependency. The dependencies had not
changed, the Auditor had built the same images two hours before, and they resolved again on 13.11. — a failed
download. Yet the sentence said „Napíš, čo treba opraviť (Uprav)“ — a Staviteľ told so would hunt a bug that is not
there. Director, on the finding and the proposal: „Súhlasím“.
"""

from __future__ import annotations

import pytest

from backend.api.routes.pipeline import _board
from backend.db.models.pipeline import PipelineMessage
from backend.services import orchestrator
from backend.tests.integration.test_fix_round_is_remeasured import _seed

FILLER = "".join(f"#14 {i:.3f} Collecting package-{i} (from -r /tmp/requirements.txt)\n" for i in range(60))
TAIL = (
    '------\n > [migrate 4/7] RUN python -c "import tomllib; …" > /tmp/requirements.txt && pip install --no-cache-dir '
    '-r /tmp/requirements.txt:\n--------------------\ntarget migrate: failed to solve: process "/bin/sh -c …" did not '
    "complete successfully: exit code: 1"
)
DNS_LINE = (
    "#14 3.112 WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after "
    "connection broken by 'NewConnectionError('<pip._vendor.urllib3.connection.HTTPSConnection object>: "
    "Failed to establish a new connection: [Errno -3] Temporary failure in name resolution')': /simple/fastapi/"
)
NO_VERSION = "#14 9.201 ERROR: No matching distribution found for fastapi<1,>=0.115"
DOWNLOAD = f"up exit 1: {DNS_LINE}\n{NO_VERSION}\n{FILLER}{TAIL}"
TYPO = (
    "up exit 1: #14 2.004 ERROR: Could not find a version that satisfies the requirement fastapy==1.0 (from versions: "
    f"none)\n#14 2.005 ERROR: No matching distribution found for fastapy==1.0\n{FILLER}{TAIL}"
)


# ── the excerpt keeps the cause ─────────────────────────────────────────────────────────────────────────────────────


def test_the_excerpt_keeps_the_error_lines_and_the_end():
    excerpt = orchestrator._build_failure_excerpt(DOWNLOAD[len("up exit 1: ") :])

    assert "Temporary failure in name resolution" in excerpt
    assert NO_VERSION.strip() in excerpt
    assert excerpt.rstrip().endswith("did not complete successfully: exit code: 1")
    assert "Collecting package-5 " not in excerpt  # the noise between stays out
    assert len(excerpt) <= 15 * 300 + 400 + 10


def test_without_an_error_line_the_excerpt_is_the_end_as_before():
    plain = "line\n" * 300 + "the end"
    assert orchestrator._build_failure_excerpt(plain) == plain.strip()[-400:]


# ── a failed download is told from a broken build ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        DNS_LINE,
        "npm ERR! code ECONNRESET\nnpm ERR! network aborted",
        "WARNING: Retrying … ReadTimeoutError(\"HTTPSConnectionPool(host='files.pythonhosted.org'): Read timed out.\")",
        "failed to resolve source metadata for docker.io/library/python:3.12: toomanyrequests: You have reached your "
        "pull rate limit",
    ],
)
def test_a_failed_download_is_recognised(line):
    assert orchestrator.is_download_failure(f"up exit 1: {line}\n{TAIL}")


@pytest.mark.parametrize(
    "detail",
    [
        TYPO,  # a wrong package name is the code's business
        f"up exit 1: SyntaxError: invalid syntax\n{TAIL}",
        "kontajner zlyhal: backend (exit 1)\n--- backend ---\nConnection refused to db while starting",  # not a build
    ],
)
def test_a_broken_build_is_no_download_failure(detail):
    assert not orchestrator.is_download_failure(detail)


# ── the start check after a fix ─────────────────────────────────────────────────────────────────────────────────────


def _measured(monkeypatch, *results):
    left = list(results)
    calls = []

    async def _app_starts_after_fix(slug):
        calls.append(slug)
        return left.pop(0)

    monkeypatch.setattr(orchestrator, "_app_starts_after_fix", _app_starts_after_fix)
    return calls


def _last(db, version_id) -> PipelineMessage:
    return (
        db.query(PipelineMessage)
        .filter(PipelineMessage.version_id == version_id)
        .order_by(PipelineMessage.seq.desc())
        .first()
    )


async def test_a_failed_download_is_retried_once_and_a_good_second_try_hands_over(db_session, monkeypatch):
    version, state = _seed(db_session)
    calls = _measured(monkeypatch, (False, DOWNLOAD), (True, "aplikácia sa spustila"))

    blocked = await orchestrator._settle_fix_boot_recheck(db_session, state)

    assert blocked is False and len(calls) == 2
    assert state.block_reason is None
    assert _last(db_session, version.id).content.startswith("Kontrola po oprave — aplikácia sa spustila.")


async def test_a_download_failing_twice_says_so_and_offers_the_retry_without_writing(db_session, monkeypatch):
    version, state = _seed(db_session)
    calls = _measured(monkeypatch, (False, DOWNLOAD), (False, DOWNLOAD))

    blocked = await orchestrator._settle_fix_boot_recheck(db_session, state)

    assert blocked is True and len(calls) == 2
    assert (state.status, state.block_reason) == ("blocked", "check_failed")
    msg = _last(db_session, version.id)
    assert msg.content == orchestrator.DOWNLOAD_FAILED_CONTENT
    assert msg.payload["download_failed"] is True
    assert "Temporary failure in name resolution" in msg.payload["technical_detail"]
    assert state.next_action == orchestrator.DOWNLOAD_FAILED_NEXT_ACTION
    assert "Skús znova" in state.next_action and "Uprav" not in state.next_action


async def test_a_broken_build_is_measured_once_and_goes_back_to_the_stavitel_as_before(db_session, monkeypatch):
    version, state = _seed(db_session)
    calls = _measured(monkeypatch, (False, TYPO))

    blocked = await orchestrator._settle_fix_boot_recheck(db_session, state)

    assert blocked is True and len(calls) == 1
    msg = _last(db_session, version.id)
    assert "napíš agentovi, čo s tým (Uprav)" in msg.content
    assert msg.payload["download_failed"] is False


def test_the_text_for_people_names_no_code_words():
    for text in (orchestrator.DOWNLOAD_FAILED_CONTENT, orchestrator.DOWNLOAD_FAILED_NEXT_ACTION):
        assert "pip" not in text and "docker" not in text.lower()


# ── the board tells the screen, so „Skús znova“ sends no bug hunt ───────────────────────────────────────────────────


@pytest.mark.parametrize(("measured", "expected"), [((DOWNLOAD, DOWNLOAD), True), ((TYPO,), False)])
async def test_the_board_says_whether_the_check_failed_on_a_download(db_session, monkeypatch, measured, expected):
    version, state = _seed(db_session)
    _measured(monkeypatch, *[(False, d) for d in measured])
    await orchestrator._settle_fix_boot_recheck(db_session, state)

    assert _board(db_session, version.id).check_failed_on_download is expected
    state.status, state.block_reason = "agent_working", None
    db_session.flush()
    assert _board(db_session, version.id).check_failed_on_download is False  # only while it stands blocked


# ── the Staviteľ hears it was no code ───────────────────────────────────────────────────────────────────────────────


async def test_after_a_failed_download_the_next_round_tells_the_stavitel_to_change_no_code(db_session, monkeypatch):
    version, state = _seed(db_session)
    _measured(monkeypatch, (False, DOWNLOAD), (False, DOWNLOAD))
    await orchestrator._settle_fix_boot_recheck(db_session, state)
    seen = []

    async def _run_build_round(db, st, *, on_event=None, directive=None, on_message=None):
        seen.append(directive)
        return st

    monkeypatch.setattr(orchestrator, "_run_build_round", _run_build_round)
    state.status = "agent_working"
    db_session.flush()
    retry = "Manažér ťa vrátil na úpravu fázy 'programovanie': Skús to znova."

    await orchestrator.run_dispatch(db_session, version.id, directive=retry)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="Úloha dokončená.",
        payload={},
    )
    state.status = "agent_working"
    db_session.flush()
    await orchestrator.run_dispatch(db_session, version.id, directive=retry)

    assert seen[0].startswith(orchestrator.DOWNLOAD_FAILED_NOTICE) and seen[0].endswith(retry)
    assert seen[1] == retry  # the Staviteľ answered since — told once
