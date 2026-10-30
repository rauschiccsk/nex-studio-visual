"""DEV-260 — every task leaves the main branch green: the app starts and the checks pass, as CI runs them.

30.10.2026 NEX Knowledge 0.2.0, task #10.1.1, dropped the old sign-in and settings tables with their models while 23
test files still imported them, and said so: „Aplikácia však teraz nenaštartuje a CI bude červená, kým ďalšie úlohy
neprepoja prihlásenie a nastavenia na šablónu.“ Nothing in the agent's rules forbade it. The Manažér got an alarm for
a state the agent had announced; while the branch stayed red, the CI of the next tasks could not show what else broke;
a build stopped half-way would leave an app that does not start. Director: „Súhlasím, založ tiket a oprav.“

The rule goes where every task reads it — the quality rules of each task's brief, both lanes, so a build already
running gets it from its next task — and into the plan, where the split is decided.
"""

from __future__ import annotations

from backend.db.models.tasks import Task
from backend.services import orchestrator


def test_every_task_brief_of_both_lanes_carries_the_rule(db_session):
    task = Task(number=1, title="Tabuľky šablóny", task_type="migration", status="todo")
    for flow in ("new_version", "fast_fix"):
        brief = orchestrator._directive_for_build_task(task, None, [], flow)
        assert orchestrator.GREEN_MAIN_RULE in brief, flow
    assert orchestrator.GREEN_MAIN_RULE in orchestrator.QUALITY_RULES


def test_the_rule_says_what_stays_and_who_removes_the_old_part():
    rule = orchestrator.GREEN_MAIN_RULE
    assert rule.startswith("HLAVNÁ VETVA PO ÚLOHE OSTÁVA ZELENÁ.")
    assert "aplikácia štartuje a skúšky, lint aj stavba prejdú tak, ako ich spúšťa CI" in rule
    assert "odstráni ju až úloha, ktorá ju nahrádza" in rule
    assert "`kind=question`" in rule


def test_the_plan_is_split_so_the_branch_stays_green():
    skeleton = orchestrator._task_plan_skeleton_directive()
    feat = orchestrator._task_plan_feat_directive("Prihlásenie cez šablónu")
    assert orchestrator.GREEN_MAIN_PLAN_RULE in skeleton
    assert orchestrator.GREEN_MAIN_PLAN_RULE in feat
    assert orchestrator.GREEN_MAIN_PLAN_RULE in orchestrator._task_plan_skeleton_directive("Pozn. Manažéra")
    assert "staré časti odstráni až úloha, ktorá ich nahrádza" in orchestrator.GREEN_MAIN_PLAN_RULE
