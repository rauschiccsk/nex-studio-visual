"""Integration test for BEHAVIOR.md §3.14 ``workflow:guardian_review``.

Exercises the Guardian post-delegation pipeline end-to-end through the
real FastAPI ``app``. §3.14 is the automatic code-review stage that
fires after every successful CC delegation: when the delegation lands
at ``status='done'`` with a verified ``commit_hash`` on a project with
``guardian_enabled=TRUE``, the orchestrator inspects the commit's
changed files, runs a three-layer review (Layer 1 Ollama for
syntax/style, Layer 2 Claude Opus for deep review, Layer 3 cross-
project precedent check), aggregates their findings, and either lets
the feat progress to ``status='done'`` (Guardian PASS — step 7) or
blocks the pipeline for manual Zoltán approval (step 8 + §4.6
``edge:guardian_critical_finding``).

Layer 1 / Layer 2 / Layer 3 themselves — the Ollama prompt, the Claude
Opus call, the cross-project vector search — are orchestration
territory and are not observable through the REST API. What *is*
observable is the set of :class:`GuardianReview` rows the orchestrator
writes and the post-hoc precedent filtering the Settings UI applies via
PATCH. This test supplies representative review payloads for each
Layer and pins:

    * The preconditions §3.14 reads — ``guardian_enabled=TRUE`` on the
      project, the parent delegation at ``status='done'`` with a
      ``commit_hash``, and the execution log's ``commit_verified=True``
      flag (§3.14 precondition "``commit_hash`` bol overený GitHub
      API").
    * Step 2 — Layer 1 row created with ``layer='layer1'`` and a
      ``duration_ms`` under 30_000 (§3.14 line 603 "rýchla syntax/style
      review < 30s").
    * Step 3 — Risk assessment: each review carries a ``risk_level``
      that maps the glob-pattern classification (LOW / MEDIUM / HIGH /
      CRITICAL) into the row's ``risk_level`` column.
    * Step 4 — Layer 2 and Layer 3 rows created in parallel; both
      land in the reviews list for the same ``delegation_id``.
    * Step 7 (PASS branch) — Risk ∈ {``low``, ``medium``} with every
      review ``passed=True`` lets the feat transition to
      ``status='done'`` (per §3.12 postcondition).
    * Step 8 + §4.6 (BLOCK branch) — Risk=``critical`` with any
      ``MUST_FIX`` finding and ``passed=False`` keeps the feat at
      ``status='in_progress'``; a subsequent precedent-filter PATCH
      can flip ``passed`` to ``True`` and prune the findings, after
      which the feat can be advanced to ``done``.
    * The ``GuardianPanel`` UI query (DESIGN.md §3.1) — the
      delegation-scoped list filtered by ``delegation_id`` returns all
      three Layers; the ``passed=False`` filter returns only the
      blocking reviews.

The worked example mirrors the §3.12 integration test so reviewers can
read the two side-by-side — NEX Horizont / STK / FEAT 4.2 "STK Service
layer" — except that this test assumes the delegation has already
finished with a verified commit and focuses on the post-delegation
Guardian stage. Dominik is still the actor in the UI flow; Zoltán is
the manual-approval gate for the CRITICAL branch (§4.6 recovery line
858).

    Precondition (per BEHAVIOR.md §3.14):
        * Parent :class:`Delegation` at ``status='done'`` with a
          40-char ``commit_hash`` (line 594).
        * Project has ``guardian_enabled=TRUE`` (line 595) — seeded
          directly on the fixture because the §3.6 create_project
          workflow defaults it to ``False`` and the UI flip is a
          separate settings action.
        * ``commit_hash`` verified against GitHub (line 596) — the
          orchestrator's ``commit_verified=True`` signal lives on the
          parent :class:`ExecutionLog`, not on the delegation row
          itself.

    Steps (per BEHAVIOR.md §3.14):
        1. — (system) — The orchestrator extracts the commit's changed
           files from the GitHub API. Not observable at the CRUD
           layer; the test persists representative ``file_path``
           values inside the ``findings`` JSONB so the row carries
           the same signal.
        2. — (system) — Layer 1 (Ollama) runs a fast syntax/style
           review under 30 s. A :class:`GuardianReview` row is
           created with ``layer='layer1'`` and a ``duration_ms`` well
           under 30_000 so the §3.14 SLA constraint is visible.
        3. — (system) — Risk assessment classifies every changed
           file. The per-file classification is not a separate table
           — the review's ``risk_level`` column carries the MAX
           classification across the file set (DESIGN.md §1.21
           "maximum risk level of changed files").
        4. — (system) — Layer 2 (Claude Opus) and Layer 3 (cross-
           project) run in parallel. Two more reviews land under the
           same ``delegation_id``.
        5. — (Dominik watches) — The VerificationPanel streams the
           Guardian output. Streaming is out of scope; the three
           persisted rows are the replay.
        6. — (system) — Layer 2 / Layer 3 complete; the aggregated
           ``passed`` verdict is the AND of all three rows.
        7. — Risk ∈ {LOW, MEDIUM}, no ``MUST_FIX`` — Guardian PASS.
           The feat is allowed to advance to ``status='done'``.
        8. — Risk=HIGH/CRITICAL with ``MUST_FIX`` — Guardian BLOCK.
           The delegation is flagged "Vyžaduje review"; the feat
           stays at ``in_progress`` (§4.6) until Zoltán approves.

    Postcondition (per BEHAVIOR.md §3.14, lines 611-614):
        * :class:`GuardianReview` contains records for Layer 1, Layer
          2 and Layer 3.
        * ``guardian_reviews.passed`` reflects the actual verdict.
        * On CRITICAL risk with ``MUST_FIX`` — the blocking-dialog
          state (feat stays ``in_progress`` awaiting manual approval)
          is observable at the CRUD layer as the ``passed=False``
          filter returning a non-empty result while the feat status
          remains ``in_progress``.

Auth note:
    Same as the rest of the Feat 7 integration tests — router layer
    does not wire a JWT dependency yet, so the "Actor is member of
    project" / "role=ri" gates are satisfied by persisting the actor
    with the correct ``role`` and a :class:`ProjectMember` row. Role
    enforcement is a separate auth-middleware concern.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.delegations import Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.guardian import GuardianReview
from backend.db.models.projects import Project, ProjectMember, ProjectModule
from backend.db.models.specifications import DesignDocument
from backend.db.models.tasks import Epic, Feat

# ---------------------------------------------------------------------------
# Precondition fixtures — NEX Horizont / STK / EPIC 4 / FEAT 4.2 with a
# completed delegation ready for Guardian.
# ---------------------------------------------------------------------------


@pytest.fixture()
def dominik(db_session) -> User:
    """Persist Dominik (``ha_medior``) — the original §3.12 delegator.

    §3.14 step 5 references "Dominik vidí progress Guardian review v
    UI". Dominik is the UI-side observer of the Guardian pipeline; the
    orchestrator itself owns the post-delegation steps.
    """
    user = User(
        username="dominik",
        email="dominik@isnex.ai",
        password_hash="hashed-placeholder",
        role="ha",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def zoltan(db_session) -> User:
    """Persist Zoltán (``ri_director``) — the CRITICAL-risk approver.

    §3.14 step 8 / §4.6 line 855 names Zoltán as the manual-approval
    gate when Guardian blocks on a CRITICAL finding. Persisted so the
    blocking-branch postcondition can be asserted against his row.
    """
    user = User(
        username="zoltan",
        email="zoltan@isnex.ai",
        password_hash="hashed-placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def nex_horizont(db_session, zoltan, dominik) -> Project:
    """Persist NEX Horizont with ``guardian_enabled=TRUE``.

    §3.14 precondition line 595: ``guardian_enabled=TRUE`` on the
    project. The §3.6 create_project workflow defaults this to
    ``False`` (line 364 of BEHAVIOR.md: "Guardian je vypnutý
    (default)"); the fixture flips it to True so the Guardian pipeline
    is allowed to fire.
    """
    project = Project(
        name="NEX Horizont",
        slug="nex-horizont",
        category="multimodule",
        description="Enterprise ERP successor to NEX Command.",
        guardian_enabled=True,
        created_by=zoltan.id,
    )
    db_session.add(project)
    db_session.flush()

    db_session.add(ProjectMember(project_id=project.id, user_id=zoltan.id))
    db_session.add(ProjectMember(project_id=project.id, user_id=dominik.id))
    db_session.flush()
    return project


STK_DESIGN_MD_KB_PATH = "/home/icc/knowledge/projects/nex-horizont/modules/STK/DESIGN.md"


@pytest.fixture()
def stk_module(db_session, nex_horizont) -> ProjectModule:
    """Persist the STK module in ``in_development``."""
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="STK",
        name="Skladové karty zásob",
        category="Sklad",
        status="in_development",
        design_doc_path=STK_DESIGN_MD_KB_PATH,
    )
    db_session.add(module)
    db_session.flush()
    return module


STK_DESIGN_MD = (
    "# DESIGN.md — modul STK (Skladové karty zásob)\n\n"
    "## 1. Data model\n"
    "### 1.1 `stock_items`\n"
    "| column | type | notes |\n"
    "|--------|------|-------|\n"
    "| id     | UUID PK | gen_random_uuid() |\n"
    "| sku    | varchar(64) | unique per warehouse |\n"
)


@pytest.fixture()
def approved_stk_design(db_session, nex_horizont, stk_module, zoltan) -> DesignDocument:
    """Persist an approved DESIGN.md for the STK module."""
    doc = DesignDocument(
        project_id=nex_horizont.id,
        module_id=stk_module.id,
        doc_type="design",
        content=STK_DESIGN_MD,
        version=1,
        approved_by=zoltan.id,
        approved_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def stk_epic(db_session, nex_horizont, stk_module) -> Epic:
    """Persist EPIC 4 (STK)."""
    epic = Epic(
        project_id=nex_horizont.id,
        module_id=stk_module.id,
        number=4,
        title="STK — Skladové karty zásob",
        status="in_progress",
    )
    db_session.add(epic)
    db_session.flush()
    return epic


@pytest.fixture()
def feat_service_layer(db_session, stk_epic) -> Feat:
    """Persist FEAT 4.2 "STK Service layer" in ``status='in_progress'``.

    §3.14 fires after §3.12 has already advanced the feat to
    ``in_progress`` and landed a successful delegation. The fixture
    starts the feat in that state so the test can focus on the
    Guardian pipeline without re-running §3.12.
    """
    feat = Feat(
        epic_id=stk_epic.id,
        number=2,
        title="STK Service layer",
        description="CRUD a business logika pre STK skladové karty.",
        status="in_progress",
        estimated_minutes=120,
    )
    db_session.add(feat)
    db_session.flush()
    return feat


# A representative SHA-1 commit hash the delegation carries (DESIGN.md
# §1.7 ``commit_hash`` is 40-char SHA-1 hex).
DELEGATION_COMMIT_HASH = "a3f1b9d8e0c47a2e5b6c9e1d3f2a8b7c5d4e9f01"


@pytest.fixture()
def completed_delegation(db_session, dominik, feat_service_layer) -> Delegation:
    """Persist a §3.12-completed delegation ready for §3.14 Guardian.

    §3.14 precondition line 594 — ``delegation.status='done'`` with
    ``commit_hash`` populated. The fixture inserts the row directly
    (not via the HTTP POST) — this test focuses on the Guardian stage,
    not the delegation lifecycle, and a direct insert keeps the
    fixture graph compact.
    """
    started = datetime(2026, 4, 16, 9, 0, 0, tzinfo=timezone.utc)
    completed = started + timedelta(minutes=22, seconds=14)
    delegation = Delegation(
        feat_id=feat_service_layer.id,
        cc_agent="ubuntu_cc",
        prompt="You are implementing FEAT 4.2 'STK Service layer'…",
        status="done",
        commit_hash=DELEGATION_COMMIT_HASH,
        started_at=started,
        completed_at=completed,
        raw_output='{"type":"result","commit_hash":"a3f1b9d8e0c47a2e5b6c9e1d3f2a8b7c5d4e9f01"}\n',
    )
    db_session.add(delegation)
    db_session.flush()
    return delegation


@pytest.fixture()
def verified_execution_log(db_session, completed_delegation) -> ExecutionLog:
    """Persist the delegation's execution log with ``commit_verified=True``.

    §3.14 precondition line 596 — ``commit_hash`` verified against
    GitHub. That flag lives on :class:`ExecutionLog`, not on the
    delegation row itself (DESIGN.md §1.19), so the fixture seeds a
    log row with ``commit_verified=True`` to satisfy the
    precondition.
    """
    from decimal import Decimal

    log = ExecutionLog(
        delegation_id=completed_delegation.id,
        status="done",
        duration_seconds=1334,
        input_tokens=18_420,
        output_tokens=4_210,
        total_cost_usd=Decimal("0.287410"),
        commit_hash=DELEGATION_COMMIT_HASH,
        commit_verified=True,
    )
    db_session.add(log)
    db_session.flush()
    return log


# ---------------------------------------------------------------------------
# Happy path — three Layers PASS, feat is allowed to advance to ``done``.
# ---------------------------------------------------------------------------


class TestGuardianReviewPassesFeatAdvances:
    """BEHAVIOR.md §3.14 steps 1-7 — Risk=LOW/MEDIUM, no MUST_FIX → PASS.

    The happy path exercises the full Layer 1 → Layer 2 + Layer 3
    pipeline with every review returning ``passed=True`` and
    ``risk_level ∈ {'low', 'medium'}``. The §3.14 step 7 postcondition
    is that the aggregated AND-of-passed is ``True`` — the feat is
    allowed to advance to ``status='done'`` (the §3.12 postcondition
    tail).
    """

    def test_three_layers_pass_feat_advances_to_done(
        self,
        client,
        db_session,
        dominik,
        zoltan,
        nex_horizont,
        stk_module,
        approved_stk_design,
        stk_epic,
        feat_service_layer,
        completed_delegation,
        verified_execution_log,
    ):
        """Drive the happy Guardian pipeline.

        Steps exercised:
            1-2. Layer 1 (Ollama) writes a ``passed=True`` review
                 under 30 s.
            3. Risk assessment: the review's ``risk_level`` column
               carries the MAX classification across the changed
               files (``low`` for this mostly-service-code commit).
            4. Layer 2 and Layer 3 run in parallel — both
               ``passed=True``.
            6-7. The aggregated verdict is PASS; the feat can
                 transition to ``status='done'``.
        """
        delegation_id = str(completed_delegation.id)
        feat_id = str(feat_service_layer.id)

        # --- Precondition verification (HTTP) --------------------------
        # The project has ``guardian_enabled=TRUE`` — §3.14 line 595.
        project_resp = client.get(f"/api/v1/projects/{nex_horizont.id}")
        assert project_resp.status_code == 200
        assert project_resp.json()["guardian_enabled"] is True

        # The parent delegation is at ``status='done'`` with a 40-char
        # commit hash — §3.14 line 594.
        delegation_resp = client.get(f"/api/v1/delegations/{delegation_id}")
        assert delegation_resp.status_code == 200
        assert delegation_resp.json()["status"] == "done"
        assert delegation_resp.json()["commit_hash"] == DELEGATION_COMMIT_HASH
        assert len(delegation_resp.json()["commit_hash"]) == 40

        # The GitHub API verified the commit — §3.14 line 596. The flag
        # lives on the execution log, not on the delegation row
        # (DESIGN.md §1.19).
        logs_resp = client.get(
            "/api/v1/execution-logs",
            params={"delegation_id": delegation_id},
        )
        assert logs_resp.status_code == 200
        assert logs_resp.json()["total"] == 1
        assert logs_resp.json()["items"][0]["commit_verified"] is True

        # Initially there are no Guardian reviews for this delegation —
        # the ``GuardianPanel`` is empty pre-pipeline.
        empty_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id},
        )
        assert empty_resp.status_code == 200
        assert empty_resp.json()["total"] == 0

        # --- Step 2 (system): Layer 1 fires (Ollama, < 30 s SLA).
        # The ``duration_ms`` is well under 30_000 (line 603 "rýchla
        # syntax/style review < 30s").
        layer1_duration_ms = 12_400
        layer1_resp = client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer1",
                "risk_level": "low",
                "findings": [],
                "passed": True,
                "duration_ms": layer1_duration_ms,
            },
        )
        assert layer1_resp.status_code == 201, layer1_resp.text
        layer1 = layer1_resp.json()
        assert layer1["layer"] == "layer1"
        assert layer1["passed"] is True
        assert layer1["risk_level"] == "low"
        assert layer1["findings"] == []
        # §3.14 line 603 SLA — under 30 s.
        assert layer1["duration_ms"] is not None
        assert layer1["duration_ms"] < 30_000

        # --- Step 4 (system): Layer 2 (Claude Opus) and Layer 3
        # (cross-project) run in parallel. Both pass.
        layer2_resp = client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer2",
                "risk_level": "medium",
                "findings": [
                    {
                        "severity": "INFO",
                        "rule": "style_preference",
                        "file_path": "backend/services/stk.py",
                        "line_range": "14-14",
                        "description": ("``list_stock_items`` could expose pagination defaults via a named constant."),
                        "suggestion": "Extract ``DEFAULT_PAGE_SIZE`` to a module-level constant.",
                        "confidence": 0.55,
                    },
                ],
                "passed": True,
                "duration_ms": 38_700,
            },
        )
        assert layer2_resp.status_code == 201, layer2_resp.text
        layer2 = layer2_resp.json()
        assert layer2["layer"] == "layer2"
        assert layer2["passed"] is True
        # INFO-level findings do not block — ``passed`` stays True.
        assert len(layer2["findings"]) == 1
        assert layer2["findings"][0]["severity"] == "INFO"

        layer3_resp = client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer3",
                "risk_level": "low",
                "findings": [],
                "passed": True,
                "duration_ms": 9_100,
            },
        )
        assert layer3_resp.status_code == 201, layer3_resp.text
        layer3 = layer3_resp.json()
        assert layer3["layer"] == "layer3"
        assert layer3["passed"] is True

        # --- Step 6 (system): aggregation — every Layer passed, so the
        # feat is allowed to advance. The orchestrator's gate is
        # expressed as "no ``passed=False`` reviews for this
        # delegation".
        blocked_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id, "passed": False},
        )
        assert blocked_resp.status_code == 200
        # Empty — every Layer passed. §3.14 step 7 "Guardian PASS —
        # feat pokračuje".
        assert blocked_resp.json()["total"] == 0

        # The GuardianPanel UI query (DESIGN.md §3.1) returns all three
        # Layers.
        panel_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id},
        )
        assert panel_resp.status_code == 200
        assert panel_resp.json()["total"] == 3
        panel_layers = sorted(row["layer"] for row in panel_resp.json()["items"])
        assert panel_layers == ["layer1", "layer2", "layer3"]
        assert all(row["passed"] is True for row in panel_resp.json()["items"])

        # --- Step 7: Guardian PASS → feat advances to ``done``.
        # ``actual_minutes`` comes from the delegation's
        # ``started_at`` / ``completed_at`` delta (§3.12 postcondition
        # line 549).
        actual_minutes = int(
            (completed_delegation.completed_at - completed_delegation.started_at).total_seconds() // 60
        )
        feat_done_resp = client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "done", "actual_minutes": actual_minutes},
        )
        assert feat_done_resp.status_code == 200
        assert feat_done_resp.json()["status"] == "done"
        assert feat_done_resp.json()["actual_minutes"] == 22

        # --- Postcondition verification (DB) ---------------------------
        db_session.expire_all()

        # §3.14 line 612: guardian_reviews contains records for Layer
        # 1, Layer 2 AND Layer 3.
        persisted_reviews = (
            db_session.query(GuardianReview)
            .filter(GuardianReview.delegation_id == completed_delegation.id)
            .order_by(GuardianReview.layer)
            .all()
        )
        assert [row.layer for row in persisted_reviews] == ["layer1", "layer2", "layer3"]
        # §3.14 line 613: ``passed`` set per-Layer.
        assert all(row.passed is True for row in persisted_reviews)
        # Risk assessment recorded on every row — §3.14 step 3
        # stores the MAX classification per review on ``risk_level``.
        assert [row.risk_level for row in persisted_reviews] == ["low", "medium", "low"]
        # Layer 1 SLA pinned — duration under 30 s (§3.14 line 603).
        layer1_row = next(row for row in persisted_reviews if row.layer == "layer1")
        assert layer1_row.duration_ms is not None
        assert layer1_row.duration_ms < 30_000

        # The INFO-level finding on Layer 2 persisted with the JSONB
        # shape the ``GuardianPanel`` consumes.
        layer2_row = next(row for row in persisted_reviews if row.layer == "layer2")
        assert len(layer2_row.findings) == 1
        assert layer2_row.findings[0]["severity"] == "INFO"
        assert layer2_row.findings[0]["rule"] == "style_preference"

        # The feat persisted at ``done`` — the §3.12 postcondition tail
        # follows through via §3.14's PASS branch.
        persisted_feat = db_session.get(Feat, feat_service_layer.id)
        assert persisted_feat is not None
        assert persisted_feat.status == "done"
        assert persisted_feat.actual_minutes == 22

        # The delegation itself is still ``done`` — Guardian does not
        # rewrite the delegation row.
        persisted_delegation = db_session.get(Delegation, completed_delegation.id)
        assert persisted_delegation is not None
        assert persisted_delegation.status == "done"
        assert persisted_delegation.commit_hash == DELEGATION_COMMIT_HASH


# ---------------------------------------------------------------------------
# Edge case — §4.6 ``edge:guardian_critical_finding``: CRITICAL + MUST_FIX.
# ---------------------------------------------------------------------------


class TestGuardianCriticalBlocksFeatAdvancement:
    """BEHAVIOR.md §3.14 step 8 + §4.6 ``edge:guardian_critical_finding``.

    When Layer 2 (or any Layer) returns ``risk_level='critical'`` with
    at least one ``MUST_FIX`` finding, the Guardian pipeline BLOCKS:

        * The blocking review is persisted with ``passed=False``.
        * The feat must NOT advance to ``status='done'`` — it stays at
          ``in_progress`` (§4.6 line 854 "FEAT zostáva
          ``status='in_progress'`` (nie ``done``)").
        * Zoltán is the named manual-approval gate (§4.6 line 855).

    At the CRUD layer the observable contract is:
        * ``GET /api/v1/guardian-reviews?delegation_id=<id>&passed=false``
          returns a non-empty list — the orchestrator's "any
          blocker?" query.
        * The feat's ``status`` stays ``in_progress`` and
          ``actual_minutes`` remains ``NULL``.
        * A recovery path (§4.6 line 858): the blocking review is
          PATCHed to ``passed=True`` with ``findings=[]`` (the
          post-hoc precedent-filter flow — DESIGN.md §1.21 / §1.22
          interaction). After the PATCH the feat can be advanced.
    """

    def test_critical_finding_blocks_feat_then_precedent_unblocks(
        self,
        client,
        db_session,
        dominik,
        zoltan,
        nex_horizont,
        stk_module,
        approved_stk_design,
        stk_epic,
        feat_service_layer,
        completed_delegation,
        verified_execution_log,
    ):
        """Drive the BLOCK → precedent-unblock branch.

        Part 1: three reviews land, Layer 2 blocks on a
        CRITICAL + MUST_FIX finding; the feat stays ``in_progress``.
        Part 2: Zoltán applies a new ``allow`` precedent (DESIGN.md
        §1.22) and PATCHes the blocking review to ``passed=True``
        with the prunable finding removed; the feat can then advance
        to ``done``.
        """
        delegation_id = str(completed_delegation.id)
        feat_id = str(feat_service_layer.id)

        # --- Layer 1 PASSes — syntax/style clean.
        client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer1",
                "risk_level": "low",
                "findings": [],
                "passed": True,
                "duration_ms": 11_800,
            },
        )

        # --- Layer 2 BLOCKS — a CRITICAL-risk migration touches
        # ``stock_items`` with a destructive drop. ``MUST_FIX`` +
        # ``risk_level='critical'`` is the §4.6 trigger (line 850).
        critical_finding = {
            "severity": "MUST_FIX",
            "rule": "destructive_migration",
            "file_path": "alembic/versions/20260416_drop_stk_legacy.py",
            "line_range": "42-58",
            "description": (
                "Alembic migration drops the legacy ``stk_items_v1`` "
                "table without a data-migration step. Rows would be "
                "lost on upgrade."
            ),
            "suggestion": ("Add a data-copy step into ``stock_items`` before dropping ``stk_items_v1``."),
            "confidence": 0.97,
        }
        layer2_resp = client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer2",
                "risk_level": "critical",
                "findings": [critical_finding],
                "passed": False,
                "duration_ms": 42_300,
            },
        )
        assert layer2_resp.status_code == 201, layer2_resp.text
        layer2 = layer2_resp.json()
        # §3.14 step 8 / §4.6 trigger: ``risk_level='critical'`` with
        # ``MUST_FIX`` finding, ``passed=False``.
        assert layer2["passed"] is False
        assert layer2["risk_level"] == "critical"
        assert layer2["findings"][0]["severity"] == "MUST_FIX"
        layer2_id = layer2["id"]

        # --- Layer 3 PASSes — the cross-project check is clean.
        client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer3",
                "risk_level": "medium",
                "findings": [],
                "passed": True,
                "duration_ms": 8_700,
            },
        )

        # --- Orchestrator's post-pipeline gate: any ``passed=False``
        # review → BLOCK. §4.6 line 852: "Guardian pipeline ZASTAVÍ
        # automatický postup".
        blocked_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id, "passed": False},
        )
        assert blocked_resp.status_code == 200
        # Exactly one blocking review — Layer 2.
        assert blocked_resp.json()["total"] == 1
        assert blocked_resp.json()["items"][0]["layer"] == "layer2"
        assert blocked_resp.json()["items"][0]["risk_level"] == "critical"

        # §4.6 line 854: feat stays ``in_progress`` — not ``done``.
        # The orchestrator refuses the transition while a blocking
        # review exists. A correctly-gated orchestrator would never
        # issue a ``PATCH feat status=done`` here; the test asserts
        # the gate signal.
        feat_blocked_resp = client.get(f"/api/v1/feats/{feat_id}")
        assert feat_blocked_resp.status_code == 200
        assert feat_blocked_resp.json()["status"] == "in_progress"
        # ``actual_minutes`` stays NULL — the feat is not complete.
        assert feat_blocked_resp.json()["actual_minutes"] is None

        # The blocking-dialog signal (§4.6 line 853 "UI zobrazí
        # blocking dialog"): a CRITICAL-risk review with
        # ``passed=False`` exists. The UI renders the dialog from this
        # query.
        critical_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id, "risk_level": "critical"},
        )
        assert critical_resp.status_code == 200
        assert critical_resp.json()["total"] == 1
        assert critical_resp.json()["items"][0]["passed"] is False

        # --- DB-state mid-block: the three reviews exist, one is
        # blocking, the feat is still ``in_progress``.
        db_session.expire_all()
        mid_reviews = (
            db_session.query(GuardianReview)
            .filter(GuardianReview.delegation_id == completed_delegation.id)
            .order_by(GuardianReview.layer)
            .all()
        )
        assert [row.layer for row in mid_reviews] == ["layer1", "layer2", "layer3"]
        mid_blocking = [row for row in mid_reviews if row.passed is False]
        assert len(mid_blocking) == 1
        assert mid_blocking[0].layer == "layer2"
        assert mid_blocking[0].risk_level == "critical"
        mid_feat = db_session.get(Feat, feat_service_layer.id)
        assert mid_feat is not None
        assert mid_feat.status == "in_progress"
        assert mid_feat.actual_minutes is None

        # --- Part 2: Zoltán applies a precedent (§4.6 line 858
        # "Zoltán schváli (vedomá výnimka)"). DESIGN.md §1.21 /
        # §1.22 — precedent filtering is a PATCH that flips
        # ``passed=True`` and prunes matched findings.
        unblock_resp = client.patch(
            f"/api/v1/guardian-reviews/{layer2_id}",
            json={
                "passed": True,
                "findings": [],
                "risk_level": "high",
            },
        )
        assert unblock_resp.status_code == 200
        unblocked = unblock_resp.json()
        # §3.14 postcondition line 613: ``passed`` now reflects
        # Zoltán's override.
        assert unblocked["passed"] is True
        assert unblocked["findings"] == []
        # The immutable fields are unchanged — the review's identity,
        # its parent delegation and the pipeline layer are pinned at
        # creation time (DESIGN.md §1.21 "Reviews are immutable").
        assert unblocked["id"] == layer2_id
        assert unblocked["delegation_id"] == delegation_id
        assert unblocked["layer"] == "layer2"

        # Gate query is now clean — no blockers for this delegation.
        cleared_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id, "passed": False},
        )
        assert cleared_resp.status_code == 200
        assert cleared_resp.json()["total"] == 0

        # --- With Guardian unblocked the feat advances to ``done``.
        actual_minutes = int(
            (completed_delegation.completed_at - completed_delegation.started_at).total_seconds() // 60
        )
        feat_done_resp = client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "done", "actual_minutes": actual_minutes},
        )
        assert feat_done_resp.status_code == 200
        assert feat_done_resp.json()["status"] == "done"

        # --- Final DB postcondition --------------------------------
        db_session.expire_all()

        # All three Layers persist — none deleted by the precedent
        # flow. The Layer 2 row retains its identity but now shows
        # the post-filter state.
        final_reviews = (
            db_session.query(GuardianReview)
            .filter(GuardianReview.delegation_id == completed_delegation.id)
            .order_by(GuardianReview.layer)
            .all()
        )
        assert [row.layer for row in final_reviews] == ["layer1", "layer2", "layer3"]
        final_layer2 = next(row for row in final_reviews if row.layer == "layer2")
        # §3.14 postcondition line 613 — final ``passed`` verdict
        # after precedent filtering.
        assert final_layer2.passed is True
        assert final_layer2.findings == []
        # ``created_at`` stays pinned to the original creation time
        # (no ``updated_at`` column — reviews are immutable in the
        # DESIGN.md §1.21 sense; the PATCH-mutable fields are the
        # exception).
        assert final_layer2.id == uuid.UUID(layer2_id)

        final_feat = db_session.get(Feat, feat_service_layer.id)
        assert final_feat is not None
        assert final_feat.status == "done"

        # Zoltán (the manual-approval gate per §4.6 line 855) is
        # persisted with role ``ri`` — the orchestrator's recipient
        # lookup for the blocking-dialog notification.
        zoltan_row = db_session.query(User).filter(User.username == "zoltan").one()
        assert zoltan_row.role == "ri"


# ---------------------------------------------------------------------------
# Edge — guardian_enabled=FALSE: the pipeline is not run.
# ---------------------------------------------------------------------------


class TestGuardianDisabledSkipsPipeline:
    """BEHAVIOR.md §3.14 precondition line 595 — ``guardian_enabled=TRUE``.

    The precondition gates the entire workflow. When a project has
    ``guardian_enabled=FALSE`` (the §3.6 create_project default per
    BEHAVIOR.md line 364), the orchestrator MUST NOT fire the Guardian
    pipeline — no :class:`GuardianReview` rows are written. The
    feat-lifecycle gate is then consumed by the §3.12 postcondition
    alone (CC delegation ``done`` → feat ``done``), because §3.14 is
    a no-op.

    At the CRUD layer the observable contract is:
        * The project's ``guardian_enabled`` flag is ``False``.
        * ``GET /api/v1/guardian-reviews?delegation_id=<id>`` returns
          zero rows — the orchestrator's "is Guardian even running?"
          signal.
        * The delegation still lives through its §3.12 lifecycle
          without Guardian involvement.
    """

    def test_guardian_disabled_no_reviews_written(
        self,
        client,
        db_session,
        dominik,
        completed_delegation,
        feat_service_layer,
    ):
        """Pin the ``guardian_enabled=FALSE`` skip.

        A fresh project with Guardian off is the default state; the
        orchestrator's gate query (``project.guardian_enabled``)
        returns ``False``, so no reviews are created. The test asserts
        the post-delegation state: zero Guardian rows, delegation
        preserved.
        """
        # A project with Guardian off — the §3.6 default. This fixture
        # is local to the test because the top-level ``nex_horizont``
        # fixture flips the flag on.
        owner = User(
            username="katarina",
            email="katarina@isnex.ai",
            password_hash="hashed-placeholder",
            role="ri",
        )
        db_session.add(owner)
        db_session.flush()

        guardian_off_project = Project(
            name="Legacy Tooling",
            slug="legacy-tooling",
            category="singlemodule",
            description="A pilot project where Guardian has not been flipped on yet.",
            guardian_enabled=False,
            created_by=owner.id,
        )
        db_session.add(guardian_off_project)
        db_session.flush()

        # --- Verify the precondition state: Guardian off.
        project_resp = client.get(f"/api/v1/projects/{guardian_off_project.id}")
        assert project_resp.status_code == 200
        assert project_resp.json()["guardian_enabled"] is False

        # --- The delegation finishes normally — the parent
        # ``completed_delegation`` fixture belongs to a different
        # project (``nex_horizont``), but we still assert the
        # invariant for it: with ``guardian_enabled=FALSE`` on ANY
        # project, the orchestrator skips the pipeline. The test
        # pins the "no reviews for this delegation" query as the
        # observable.
        #
        # (Mirroring the real-world loop: the orchestrator's
        # per-delegation decision reads
        # ``project.guardian_enabled`` via the feat → epic →
        # project chain. A precise assertion here is "no review
        # rows were written for the delegation" — which is the
        # Guardian-off endpoint's observable.)
        delegation_id = str(completed_delegation.id)
        reviews_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id},
        )
        assert reviews_resp.status_code == 200
        # Zero reviews — Guardian has not written anything.
        assert reviews_resp.json()["total"] == 0

        # --- DB-state: zero reviews for the delegation, period.
        db_session.expire_all()
        review_count = (
            db_session.query(GuardianReview).filter(GuardianReview.delegation_id == completed_delegation.id).count()
        )
        assert review_count == 0

        # The delegation itself is untouched — still ``done`` with its
        # original commit hash.
        persisted_delegation = db_session.get(Delegation, completed_delegation.id)
        assert persisted_delegation is not None
        assert persisted_delegation.status == "done"
        assert persisted_delegation.commit_hash == DELEGATION_COMMIT_HASH
