"""Model price list read off the turns that Claude Code paid for (ICCINT-168, :mod:`model_pricing`).

Observed prices are real price lists measured on 06.10.2026 by solving equations against Claude Code ``costUSD``:
Opus 5.5 4 / 20 / 0.20 / 8 (cache read = 5 % of input!), Opus 5 5 / 25 / 0.50 / 10, Haiku 4.5 1 / 5 / 0.10 / 2.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.db.models.foundation import User
from backend.db.models.model_price import ModelPrice
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import model_pricing
from backend.services.model_pricing import Observation, Prices, Rate

OPUS_55 = Prices(4.0, 20.0, 0.2, 8.0)
OPUS_5 = Prices(5.0, 25.0, 0.5, 10.0)
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)

#: Shape of the turns from a build: only units of non-cache input (hence the equations come out imprecise), output
#: hundreds to thousands, cache reads tens to hundreds of thousands, writes thousands.
SHAPES = [
    (2, 149, 21464, 0),
    (2, 3400, 12073, 9410),
    (2, 4, 12073, 7516),
    (6, 812, 154_000, 2_200),
    (3, 2_950, 310_000, 11_400),
    (9, 77, 98_000, 640),
    (1, 1_500, 45_000, 30_000),
]


def _obs(prices: Prices, shapes=SHAPES, start=T0) -> list[Observation]:
    return [
        Observation(start + timedelta(minutes=i), tokens, round(prices.cost_usd(tokens), 7))
        for i, tokens in enumerate(shapes)
    ]


def test_the_price_list_comes_out_exactly_as_anthropic_lists_it():
    """Non-cache input is only a few tokens in a turn, so the equations come out imprecise (Dedo Home: 4.9938); after
    rounding to the coarsest step that Claude Code prices still reproduce, it is exactly the price list."""
    assert model_pricing.fit_prices(_obs(OPUS_55)) == OPUS_55
    assert model_pricing.fit_prices(_obs(OPUS_5)) == OPUS_5


def test_no_fixed_ratio_is_assumed_between_cache_and_input():
    """Opus 5.5 reads the cache at 5 % of input, Haiku at 10 % — the ratio comes from the numbers, not an assumption."""
    haiku = Prices(1.0, 5.0, 0.1, 2.0)
    fitted = model_pricing.fit_prices(_obs(haiku))
    assert fitted == haiku
    assert model_pricing.fit_prices(_obs(OPUS_55)).cache_read_usd / OPUS_55.input_usd == pytest.approx(0.05)


def test_too_few_or_dependent_turns_give_no_price_list():
    """Four prices need at least four independent turns — otherwise the price list would be made up."""
    assert model_pricing.fit_prices(_obs(OPUS_55)[:3]) is None
    same_shape = [Observation(T0 + timedelta(minutes=i), (2, 100, 1000, 10), 0.1) for i in range(6)]
    assert model_pricing.fit_prices(same_shape) is None
    no_cache_writes = [(2, 100 + i, 1000 * i, 0) for i in range(1, 7)]
    assert model_pricing.fit_prices(_obs(OPUS_55, no_cache_writes)) is None  # the write cannot be determined


def test_turns_two_price_lists_cannot_explain_together_give_none():
    """A mix of two price lists is not reproduced by a single price list — better none than a wrong one."""
    mixed = _obs(OPUS_55, SHAPES[:4]) + _obs(OPUS_5, SHAPES[4:], start=T0 + timedelta(days=1))
    assert model_pricing.fit_prices(mixed) is None


def test_deviations_measure_the_list_against_claude_code():
    total, worst = model_pricing.deviations(OPUS_55, _obs(OPUS_55))
    assert total == pytest.approx(0.0, abs=1e-6) and worst == pytest.approx(0.0, abs=1e-5)
    total, worst = model_pricing.deviations(OPUS_5, _obs(OPUS_55))
    assert total > 0.2 and worst > 0.2


# ── kurz ───────────────────────────────────────────────────────────────────────

ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-10-05'><Cube currency='USD' rate='1.1204'/><Cube currency='JPY' rate='171.2'/></Cube></Cube>
</gesmes:Envelope>"""


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_ecb_rate_is_read_with_its_date(monkeypatch):
    monkeypatch.setattr(model_pricing.urllib.request, "urlopen", lambda url, timeout: _Resp(ECB_XML))
    assert model_pricing._fetch_ecb_rate() == Rate(1.1204, date(2026, 10, 5), model_pricing.ECB_SOURCE)


def test_an_unreachable_ecb_gives_no_rate_instead_of_breaking(monkeypatch):
    def _down(url, timeout):
        raise OSError("network unreachable")

    monkeypatch.setattr(model_pricing.urllib.request, "urlopen", _down)
    assert model_pricing._fetch_ecb_rate() is None
    monkeypatch.setattr(
        model_pricing.urllib.request, "urlopen", lambda url, timeout: _Resp(b"<html>maintenance</html>")
    )
    assert model_pricing._fetch_ecb_rate() is None


# ── price lists in the database ────────────────────────────────────────────────


@pytest.fixture()
def version(db_session):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@x.sk", password_hash="x", role="ri"
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    v = Version(project_id=project.id, version_number="1.0.0")
    db_session.add(v)
    db_session.flush()
    return v


def _turns(db_session, version, observations, model="claude-opus-5-5"):
    for o in observations:
        part = {
            "model": model,
            "input_tokens": o.tokens[0],
            "output_tokens": o.tokens[1],
            "cache_read_tokens": o.tokens[2],
            "cache_write_tokens": o.tokens[3],
            "cost_usd": o.cost_usd,
        }
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="programovanie",
                author="ai_agent",
                recipient="manazer",
                kind="gate_report",
                content="x",
                payload={"usage": {"input_tokens": 1, "output_tokens": 1, "model": model, "parts": [part]}},
                created_at=o.at,
            )
        )
    db_session.flush()


RATE = Rate(1.1204, date(2026, 10, 5), "test")


def _lists(db_session, model="claude-opus-5-5"):
    return model_pricing.price_rows(db_session).get(model, [])


def test_a_model_gets_its_list_and_rate_once_it_has_enough_paid_turns(db_session, version):
    calls = []

    def _rate():
        calls.append(1)
        return RATE

    _turns(db_session, version, _obs(OPUS_55)[:3])
    assert model_pricing.refresh(db_session, fetch_rate=_rate) == []  # three turns are not enough
    assert _lists(db_session) == [] and calls == []

    _turns(db_session, version, _obs(OPUS_55)[3:])
    assert model_pricing.refresh(db_session, fetch_rate=_rate) == ["claude-opus-5-5"]
    [row] = _lists(db_session)
    assert (row.input_usd, row.output_usd, row.cache_read_usd, row.cache_write_usd) == OPUS_55.as_tuple()
    assert (row.eur_usd, row.rate_date, row.observations) == (1.1204, date(2026, 10, 5), len(SHAPES))
    assert row.valid_from == T0  # valid from the first turn that covers it
    assert calls == [1]  # the rate was fetched once, when the price list appeared — not on every display

    model_pricing.refresh(db_session, fetch_rate=_rate)
    assert calls == [1]


def test_new_turns_that_fit_only_extend_the_list(db_session, version):
    _turns(db_session, version, _obs(OPUS_55))
    model_pricing.refresh(db_session, fetch_rate=lambda: RATE)
    later = _obs(OPUS_55, SHAPES[:2], start=T0 + timedelta(days=2))
    _turns(db_session, version, later)
    assert model_pricing.refresh(db_session, fetch_rate=lambda: RATE) == []
    [row] = _lists(db_session)
    assert row.observations == len(SHAPES) + 2 and row.checked_until == later[-1].at


def test_a_price_change_starts_a_new_list_and_keeps_the_old_one(db_session, version):
    """Anthropic changed a price: newer turns do not reproduce the old list. Two such turns do not yet determine a new
    one; once the turns from then on do, a list valid from their first appears — the old stays, it priced older ones."""
    _turns(db_session, version, _obs(OPUS_5))
    model_pricing.refresh(db_session, fetch_rate=lambda: RATE)
    change = T0 + timedelta(days=3)
    _turns(db_session, version, _obs(OPUS_55, SHAPES[:2], start=change))
    assert model_pricing.refresh(db_session, fetch_rate=lambda: RATE) == []  # two turns do not determine a price list
    _turns(db_session, version, _obs(OPUS_55, SHAPES[2:], start=change + timedelta(hours=1)))
    assert model_pricing.refresh(db_session, fetch_rate=lambda: RATE) == ["claude-opus-5-5"]
    old, new = _lists(db_session)
    assert Prices(old.input_usd, old.output_usd, old.cache_read_usd, old.cache_write_usd) == OPUS_5
    assert Prices(new.input_usd, new.output_usd, new.cache_read_usd, new.cache_write_usd) == OPUS_55
    assert new.valid_from == change
    rows = model_pricing.price_rows(db_session)
    assert model_pricing.row_for(rows, "claude-opus-5-5", change - timedelta(minutes=1)) is old
    assert model_pricing.row_for(rows, "claude-opus-5-5", change + timedelta(days=1)) is new
    assert model_pricing.row_for(rows, "claude-opus-5-5", T0 - timedelta(days=30)) is old  # older than the first turn


def test_a_list_without_a_rate_retries_the_rate(db_session, version):
    _turns(db_session, version, _obs(OPUS_55))
    model_pricing.refresh(db_session, fetch_rate=lambda: None)
    [row] = _lists(db_session)
    assert row.eur_usd is None  # ECB unavailable — the price list exists, the euros are not computed yet
    model_pricing.refresh(db_session, fetch_rate=lambda: RATE)
    assert _lists(db_session)[0].eur_usd == 1.1204


def test_unpaid_parts_never_teach_the_list(db_session, version):
    """An interrupted turn (without a Claude Code price) does not teach the price list — it is priced by it."""
    _turns(db_session, version, [Observation(o.at, o.tokens, None) for o in _obs(OPUS_55)])  # type: ignore[arg-type]
    model_pricing.refresh(db_session, fetch_rate=lambda: RATE)
    assert _lists(db_session) == []


def test_a_hand_entered_cost_naming_a_family_uses_its_newest_list(db_session):
    def _row(model, at):
        return ModelPrice(
            model=model,
            valid_from=at,
            input_usd=1,
            output_usd=1,
            cache_read_usd=1,
            cache_write_usd=1,
            observations=4,
            max_deviation=0,
            checked_until=at,
        )

    older, newer = _row("claude-opus-5", T0), _row("claude-opus-5-5", T0 + timedelta(days=5))
    rows = {"claude-opus-5": [older], "claude-opus-5-5": [newer]}
    assert model_pricing.family_row(rows, "opus") is newer
    assert model_pricing.family_row(rows, "claude-opus-5") is older
    assert model_pricing.family_row(rows, "haiku") is None


def test_one_bad_record_among_good_turns_never_makes_a_new_list(db_session, version):
    """One bad record (the price does not fit) among correct turns does not determine a new list — the turns after it
    are not explained by a single price list — and the correct turns before it are still counted as verified."""
    _turns(db_session, version, _obs(OPUS_55))
    model_pricing.refresh(db_session, fetch_rate=lambda: RATE)
    later = T0 + timedelta(days=2)
    bad = Observation(later, SHAPES[3], 0.9)  # $0.9 for a turn that costs ~$0.05
    _turns(db_session, version, [*_obs(OPUS_55, SHAPES[:2], start=later - timedelta(hours=1)), bad])
    _turns(db_session, version, _obs(OPUS_55, SHAPES[4:], start=later + timedelta(hours=1)))
    assert model_pricing.refresh(db_session, fetch_rate=lambda: RATE) == []
    [row] = _lists(db_session)
    assert row.observations == len(SHAPES) + 2  # the two correct ones before the bad record are verified


def test_a_web_search_turn_does_not_bend_the_token_prices_and_teaches_the_search_price(db_session, version):
    """Claude Code bills web search separately ($0.01) and includes it in the turn price. A turn with it learned as
    tokens would let input swallow the price (measured: Haiku $1.55 not $1) — so only the search price is learned."""
    haiku = Prices(1.0, 5.0, 0.1, 2.0)
    clean = _obs(haiku)
    tokens = (30_000, 900, 40_000, 2_000)
    search = Observation(T0 + timedelta(hours=5), tokens, round(haiku.cost_usd(tokens) + 4 * 0.01, 7), searches=4)
    _turns(db_session, version, clean, model="claude-haiku-4-5")
    part = {"model": "claude-haiku-4-5", "input_tokens": tokens[0], "output_tokens": tokens[1]}
    part.update(cache_read_tokens=tokens[2], cache_write_tokens=tokens[3], cost_usd=search.cost_usd)
    part["web_search_requests"] = 4
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="programovanie",
            author="ai_agent",
            recipient="manazer",
            kind="gate_report",
            content="x",
            payload={"usage": {"input_tokens": 1, "output_tokens": 1, "model": "m", "parts": [part]}},
            created_at=search.at,
        )
    )
    db_session.flush()
    model_pricing.refresh(db_session, fetch_rate=lambda: RATE)
    [row] = _lists(db_session, "claude-haiku-4-5")
    assert (row.input_usd, row.output_usd, row.cache_read_usd, row.cache_write_usd) == haiku.as_tuple()
    assert row.web_search_usd == 0.01


def test_after_an_ecb_failure_the_rate_is_not_retried_on_every_page_view(monkeypatch):
    """An unavailable ECB must not slow down every opening of Náklady with a 15-second wait."""
    calls = []

    def _down(timeout):
        calls.append(1)
        return None

    monkeypatch.setattr(model_pricing, "_fetch_ecb_rate", _down)
    monkeypatch.setattr(model_pricing, "_ecb_failed_at", None)
    assert model_pricing._throttled_ecb_rate(1) is None
    assert model_pricing._throttled_ecb_rate(1) is None
    assert calls == [1]  # the second attempt was deferred
    monkeypatch.setattr(model_pricing, "_ecb_failed_at", model_pricing.time.monotonic() - 601)
    monkeypatch.setattr(model_pricing, "_fetch_ecb_rate", lambda timeout: RATE)
    assert model_pricing._throttled_ecb_rate(1) == RATE  # po odklade znova


def test_one_refresh_fetches_the_rate_at_most_once(db_session, version):
    calls = []

    def _rate():
        calls.append(1)
        return None

    _turns(db_session, version, _obs(OPUS_55), model="claude-opus-5-5")
    _turns(db_session, version, _obs(OPUS_5, start=T0 + timedelta(days=1)), model="claude-opus-5")
    model_pricing.refresh(db_session, fetch_rate=_rate)
    assert len(_lists(db_session, "claude-opus-5-5")) == 1 and len(_lists(db_session, "claude-opus-5")) == 1
    assert calls == [1]
