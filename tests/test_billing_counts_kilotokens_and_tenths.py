"""DEV-263 — Fakturácia counts in kilotokens rounded up to a tenth, and every line's money in tenths of a euro.

The Director 30.10.2026: „stĺpec počet riadkov odstrániť … Tokeny vypísať v tzv kilo tokenoch "kT" 1 kT = 1000
Tokenov, summy jednotlivých riadkov zaokrúhliť na jednu desatinu (264.42 na 264.40)“; „kilo tokeny zaokrúhliť hore na
1 desatinné miesto príklad 1451.4 kT x 2 Eur = 2902.80 Eur“ (confirmed); the full value and the discount in tenths too,
half up, the amount their difference (approved). An issued statement keeps what it was issued with.
"""

from __future__ import annotations

from decimal import Decimal

from backend.core.kilotokens import kilotokens
from backend.db.models.projects import Project
from backend.schemas.delivery_statement import (
    DeliveredFileRead,
    IssuedStatementRead,
    StatementLineRead,
    StatementLineTaskRead,
)
from backend.services import billing_category as bc
from backend.services import delivery_statement as st
from tests.test_a_task_has_a_billing_category import billed  # noqa: F401 — the fixture is used by name


def test_kilotokens_are_rounded_up_to_a_tenth():
    assert kilotokens(1_451_301) == Decimal("1451.4")
    assert kilotokens(1_451_400) == Decimal("1451.4")
    assert kilotokens(1_451_401) == Decimal("1451.5")
    assert kilotokens(1) == Decimal("0.1")
    assert kilotokens(0) == Decimal("0.0")


def test_the_director_s_example_holds():
    line = bc.value(1_451_301, Decimal("2"), Decimal("0"))

    assert (line.full, line.discount, line.amount) == (Decimal("2902.8"), Decimal("0.0"), Decimal("2902.8"))


def test_the_money_of_a_line_is_in_tenths_half_up_and_the_amount_is_the_difference():
    assert bc.value(1000, Decimal("264.42"), Decimal("0")).full == Decimal("264.4")
    assert bc.value(1000, Decimal("264.45"), Decimal("0")).full == Decimal("264.5")
    discounted = bc.value(10_000, Decimal("26.44"), Decimal("10"))
    assert (discounted.full, discounted.discount, discounted.amount) == (
        Decimal("264.4"),
        Decimal("26.4"),
        Decimal("238.0"),
    )


def test_every_line_of_a_statement_is_in_tenths_and_the_total_is_their_sum(billed):  # noqa: F811
    db, root, _owner, v010, _tasks, _v011, _t2 = billed

    p = st.preview(db, v010, root)

    for ln in p.lines:
        v = ln.value
        assert v.full == v.full.quantize(Decimal("0.1")) and v.discount == v.discount.quantize(Decimal("0.1"))
        assert v.full == (kilotokens(ln.tokens) * ln.rate).quantize(Decimal("0.1"))
    assert p.amounts.total == sum((ln.value.amount for ln in p.lines), Decimal("0"))


def test_the_screen_reads_kilotokens_from_the_server():
    task = StatementLineTaskRead(label="1.1.1", title="Faktúry", lines=40, tokens=1_451_301)
    line = StatementLineRead(
        category="request", kind="code", tasked=True, label="Požiadavka zákazníka — kód a skúšky", lines=40,
        tokens=1_451_301, rate=Decimal("2"), full_eur=Decimal("2902.8"), discount_pct=Decimal("0"),
        discount_eur=Decimal("0"), amount_eur=Decimal("2902.8"), tasks=[task],
    )  # fmt: skip
    docs = line.model_copy(update={"kind": "docs", "tokens": 1_001})
    untasked = line.model_copy(update={"tasked": False, "tokens": 1})  # its own line: rounded up on its own

    assert task.model_dump()["kilotokens"] == Decimal("1451.4")
    assert line.model_dump()["kilotokens"] == Decimal("1451.4")
    assert DeliveredFileRead(path="a.py", kind="kod", lines=3, tokens=1_001).model_dump()["kilotokens"] == Decimal(
        "1.1"
    )
    issued = IssuedStatementRead.model_construct(
        tokens_code=1_451_302, tokens_tests=0, tokens_docs=1_001, lines=[line, untasked, docs]
    )
    # 1 451,4 + 0,1 + 1,1 — the kT the money was counted from; the whole delivery at once would be 1 451,4 + 1,1.
    assert issued.kilotokens == Decimal("1452.6"), "the billed kT of an issued statement is the sum of its lines"


def test_the_csv_has_no_lines_column_and_counts_in_kilotokens(billed):  # noqa: F811
    db, root, owner, v010, _tasks, _v011, _t2 = billed
    row = st.issue(db, v010, root, owner.id)

    text = st.csv_text(row, v010, db.get(Project, v010.project_id))
    rows = text.splitlines()

    assert "Riadkov" not in text
    assert "Riadok súpisu;kT;Sadzba (€ / kT);Plná hodnota (€);Zľava (%);Zľava (€);Dôvod zľavy;Suma (€)" in rows
    first = row.lines[0]
    assert f"{first['label']};{str(kilotokens(first['tokens'])).replace('.', ',')};" in text
    assert "Úloha;Názov;Riadok súpisu;kT" in rows
    assert "Súbor;Druh;kT;Neráta sa — prečo" in rows
