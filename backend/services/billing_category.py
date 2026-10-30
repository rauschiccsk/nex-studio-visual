"""DEV-75 — what each task's delivered work is billed as, the discount of each kind, and the statement's lines.

12.10.2026 the Director: besides bug fixes and customer requests he needs a gift — an attention of ICC that goes on the
invoice, where „môžeme na to dať aj 100% zľavy“; the category is decided for every task. Approved design:

* each task has a category — ``bug`` (a fix of an error in code we delivered), ``request`` (what the customer asked
  for), ``gift`` (an attention of ICC); empty = the version's own (``delivery_statement.version_category``: a fast
  fix of our error → bug, anything else → request); set for a whole epic or feat at once;
* each category has a discount in % — the defaults in Nastavenia (bug 100 as a warranty fix, request 0, gift 100), a
  version may have its own; every change is kept, who and when (as the rates of DEV-74);
* the statement splits the delivered tokens by the task whose commit wrote each line
  (:func:`backend.services.delivered_tokens.line_commits`): one line per category × (code and tests, documentation),
  work without a task (design documents, the visual phase, fixes after verification) as its own lines with the version's
  category. Each line shows its full value, the discount and the amount — the customer sees what a warranty fix or a
  gift was worth; the total is the sum of the amounts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.kilotokens import TENTH, kilotokens
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.version_discount_change import VersionDiscountChange
from backend.db.models.versions import Version
from backend.services import delivered_tokens, system_setting

BUG = "bug"
REQUEST = "request"
GIFT = "gift"
#: Also the order of the statement's lines.
CATEGORIES = (REQUEST, BUG, GIFT)

LABELS = {REQUEST: "Požiadavka zákazníka", BUG: "Oprava chyby", GIFT: "Dar — pozornosť ICC"}
#: What the discount is called on the statement.
DISCOUNT_LABELS = {REQUEST: "zľava", BUG: "záručná oprava", GIFT: "pozornosť ICC"}
#: The defaults in Nastavenia (``system_setting``).
DISCOUNT_KEYS = {BUG: "billing_discount_bug", REQUEST: "billing_discount_request", GIFT: "billing_discount_gift"}

KIND_CODE = "code"
KIND_DOCS = "docs"
KIND_LABELS = {KIND_CODE: "kód a skúšky", KIND_DOCS: "dokumentácia"}
UNTASKED_LABEL = "Práca bez úlohy"

CENT = Decimal("0.01")
HUNDRED = Decimal("100")

DISCOUNT_OUT_OF_RANGE = "Zľava musí byť od 0 do 100 % — prázdna znamená zľavu z Nastavení."
UNKNOWN_CATEGORY = "Neznáma kategória úlohy."
UNKNOWN_TASKS = "Niektorá z úloh nepatrí tejto verzii — obnov stránku."


# ── the tasks of a version ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlanTask:
    id: uuid.UUID
    #: ``epic.feat.task`` — the number the AI Agent writes into its commits as ``(TASK #20.1.1)``.
    label: str
    title: str
    #: Its own category; ``None`` = the version's.
    category: Optional[str]
    #: The epic it is under — the screen sets a whole epic's tasks at once.
    epic_title: str = ""


def plan_tasks(db: Session, version_id: uuid.UUID) -> list[PlanTask]:
    """Every task of the version's plan, in the plan's order."""
    rows = db.execute(
        select(Task, Feat.number, Epic.number, Epic.title)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version_id)
        .order_by(Epic.number, Feat.number, Task.number)
    ).all()
    return [
        PlanTask(
            id=t.id,
            label=f"{epic}.{feat}.{t.number}",
            title=t.title,
            category=t.billing_category,
            epic_title=epic_title,
        )
        for t, feat, epic, epic_title in rows
    ]


def set_categories(db: Session, version_id: uuid.UUID, task_ids: list[uuid.UUID], category: Optional[str]) -> int:
    """Give the tasks a category (``None`` = back to the version's). Every task must belong to the version — one
    that does not refuses the whole change (:class:`ValueError`). Returns how many tasks changed."""
    if category is not None and category not in CATEGORIES:
        raise ValueError(UNKNOWN_CATEGORY)
    wanted = set(task_ids)
    tasks = list(
        db.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id, Task.id.in_(wanted))
        ).scalars()
    )
    if len(tasks) != len(wanted):
        raise ValueError(UNKNOWN_TASKS)
    changed = 0
    for task in tasks:
        if task.billing_category != category:
            task.billing_category = category
            changed += 1
    db.flush()
    return changed


# ── the discounts ──────────────────────────────────────────────────────────────


def settings_discounts(db: Session) -> dict[str, Decimal]:
    """The discounts in Nastavenia, in %."""
    return {c: Decimal(str(system_setting.get_float(db, DISCOUNT_KEYS[c]))) for c in CATEGORIES}


def own_discounts(version: Version) -> dict[str, Optional[Decimal]]:
    """The version's own discounts; ``None`` = the one in Nastavenia."""
    own = {BUG: version.discount_bug, REQUEST: version.discount_request, GIFT: version.discount_gift}
    return {c: None if v is None else Decimal(v) for c, v in own.items()}


def discounts(db: Session, version: Version) -> dict[str, Decimal]:
    """The discounts a version's statement counts with: its own where it has one, else Nastavenia."""
    settings, own = settings_discounts(db), own_discounts(version)
    return {c: own[c] if own[c] is not None else settings[c] for c in CATEGORIES}


def set_discounts(
    db: Session, version: Version, values: dict[str, Optional[Decimal]], changed_by: Optional[uuid.UUID]
) -> Optional[VersionDiscountChange]:
    """Set the version's own discounts (``None`` = back to Nastavenia) and keep the change — who, when, from what to
    what. An issued statement keeps what it was issued with; only a new one replacing it counts with these. Returns
    the recorded change, ``None`` when nothing changed."""
    new: dict[str, Optional[Decimal]] = {}
    for c in CATEGORIES:
        value = values.get(c)
        if value is not None:
            value = Decimal(value)
            if not 0 <= value <= HUNDRED:
                raise ValueError(DISCOUNT_OUT_OF_RANGE)
            value = value.quantize(CENT, rounding=ROUND_HALF_UP)
        new[c] = value
    previous = own_discounts(version)
    if previous == new:
        return None
    change = VersionDiscountChange(
        version_id=version.id,
        previous_discount_bug=previous[BUG],
        previous_discount_request=previous[REQUEST],
        previous_discount_gift=previous[GIFT],
        discount_bug=new[BUG],
        discount_request=new[REQUEST],
        discount_gift=new[GIFT],
        changed_by=changed_by,
    )
    version.discount_bug, version.discount_request, version.discount_gift = new[BUG], new[REQUEST], new[GIFT]
    db.add(change)
    db.flush()
    return change


def discount_changes(db: Session, version_id: uuid.UUID) -> list[VersionDiscountChange]:
    """Every change of a version's own discounts, the newest first."""
    return list(
        db.execute(
            select(VersionDiscountChange)
            .where(VersionDiscountChange.version_id == version_id)
            .order_by(VersionDiscountChange.created_at.desc())
        ).scalars()
    )


# ── the statement's lines ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Value:
    """One line's money: its full value, the discount and what is billed — each in tenths of a euro (DEV-263)."""

    full: Decimal
    discount: Decimal

    @property
    def amount(self) -> Decimal:
        return self.full - self.discount


def value(tokens: int, rate: Decimal, discount_pct: Decimal) -> Value:
    """DEV-263: kilotokens (rounded up to a tenth) × rate, rounded half up to a tenth of a euro; the discount is a
    share of that rounded value, rounded the same way — so full value − discount = amount holds on every line of the
    invoice. The Director 30.10.2026: „1451.4 kT x 2 Eur = 2902.80 Eur“; „summy jednotlivých riadkov zaokrúhliť na
    jednu desatinu (264.42 na 264.40)“."""
    full = (kilotokens(tokens) * rate).quantize(TENTH, rounding=ROUND_HALF_UP)
    return Value(full=full, discount=(full * discount_pct / HUNDRED).quantize(TENTH, rounding=ROUND_HALF_UP))


@dataclass
class TaskShare:
    """A task of the plan with its category as it counts and what it delivered (0 when nothing)."""

    task: PlanTask
    category: Optional[str]
    lines: int = 0
    tokens: int = 0


@dataclass
class Line:
    category: str
    kind: str  # KIND_CODE · KIND_DOCS
    tasked: bool
    rate: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    lines: int = 0
    tokens: int = 0
    #: The tasks whose work the line bills — ``{label: TaskShare of this kind}``; empty for work without a task.
    tasks: dict[str, TaskShare] = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.tasked:
            return f"{LABELS[self.category]} — {KIND_LABELS[self.kind]}"
        return f"{UNTASKED_LABEL} — {KIND_LABELS[self.kind]} ({LABELS[self.category]})"

    @property
    def value(self) -> Value:
        return value(self.tokens, self.rate, self.discount_pct)

    def payload(self) -> dict:
        """The line as an issued statement keeps it — frozen, decimals as text."""
        v = self.value
        return {
            "category": self.category,
            "kind": self.kind,
            "tasked": self.tasked,
            "label": self.label,
            "lines": self.lines,
            "tokens": self.tokens,
            "rate": str(self.rate),
            "full_eur": str(v.full),
            "discount_pct": str(self.discount_pct),
            "discount_label": DISCOUNT_LABELS[self.category] if self.discount_pct else None,
            "discount_eur": str(v.discount),
            "amount_eur": str(v.amount),
            "tasks": [
                {"label": s.task.label, "title": s.task.title, "lines": s.lines, "tokens": s.tokens}
                for s in self.tasks.values()
            ],
        }


def split(
    count: delivered_tokens.Count,
    plan: list[PlanTask],
    default: str,
    rates: dict[str, Decimal],
    discount: dict[str, Decimal],
) -> tuple[list[Line], list[TaskShare]]:
    """The statement's lines and every task's share. A line written by a commit that names a task of this plan
    belongs to that task's category; anything else — no task, a task of another version — is work without a task,
    in the version's category (``default``)."""
    by_label = {t.label: t for t in plan}
    shares = {t.label: TaskShare(task=t, category=t.category or default) for t in plan}
    lines: dict[tuple[str, str, bool], Line] = {}
    for f in count.files:
        if not f.kind:
            continue
        kind = KIND_DOCS if f.kind == delivered_tokens.KIND_DOCS else KIND_CODE
        groups = f.by_task or {None: (f.lines, f.tokens)}
        for label, (n, tokens) in groups.items():
            task = by_label.get(label or "")
            category = (task.category or default) if task else default
            key = (category, kind, task is not None)
            line = lines.get(key)
            if line is None:
                line = lines[key] = Line(
                    category=category,
                    kind=kind,
                    tasked=task is not None,
                    rate=rates[kind],
                    discount_pct=discount[category],
                )
            line.lines += n
            line.tokens += tokens
            if task is not None:
                share = shares[task.label]
                share.lines += n
                share.tokens += tokens
                part = line.tasks.setdefault(task.label, TaskShare(task=task, category=category))
                part.lines += n
                part.tokens += tokens
    order = {t.label: i for i, t in enumerate(plan)}
    for line in lines.values():
        line.tasks = dict(sorted(line.tasks.items(), key=lambda item: order[item[0]]))
    ordered = sorted(
        (line for line in lines.values() if line.tokens),
        key=lambda ln: (CATEGORIES.index(ln.category), not ln.tasked, ln.kind != KIND_CODE),
    )
    return ordered, list(shares.values())
