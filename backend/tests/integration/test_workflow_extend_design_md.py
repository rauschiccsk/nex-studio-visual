"""Integration test for BEHAVIOR.md §3.11 ``workflow:extend_design_md``.

Exercises the full happy path of the **extend_design_md** workflow
end-to-end through the real FastAPI ``app``. The workflow is the
"ad-hoc, during implementation" companion to §3.5 (``approve_design_md``):
Tibor (``ri_senior``) is mid-delivery on the STK module, discovers a
new entity that wasn't captured in the already-approved DESIGN.md,
asks the Architect to propose an extension, and — after reviewing the
diff — clicks "Schváliť rozšírenie". The system updates the existing
:class:`DesignDocument` row *in place*: ``content`` grows with the new
schema, ``version`` monotonically increments (1 → 2), and the original
``approved_by`` / ``approved_at`` stamps are preserved so downstream
consumers (the Architect context-injection query, the §3.10 import
gate, etc.) still see the row as approved.

The worked example throughout is drawn from BEHAVIOR.md §3.11 step 1
verbatim: "Pridaj tabuľku ``stock_adjustments`` do DESIGN.md pre STK"
— a Slovak-language request for a new inventory-adjustments table
that Tibor needs before he can wire up the service-layer
``create_stock_adjustment`` task. The Architect proposes the SQL
schema and the insertion point in §1 "Data model" of the DESIGN.md;
Tibor approves the proposal in the chat; the system persists the
updated document and bumps the version.

The AI that synthesises the proposed extension is out of scope at
this layer — the test supplies the structured SQL block the Architect
would have returned and verifies the *observable* side effects: the
HTTP contract (the two ``architect_messages`` turns plus the
``design_documents`` PATCH), the DB row (``content`` extended,
``version`` incremented, approval stamps preserved), and the
postconditions listed under §3.11.

The filesystem write at step 3 ("Systém zapíše aktualizovaný DESIGN.md
na filesystem") is orchestration territory and out of scope at the
HTTP layer — the observable side is the module's ``design_doc_path``
remaining populated (the same contract §3.5 pins on initial approval;
the extension does not unset the path).

    Precondition (per BEHAVIOR.md §3.11):
        * Architect session is ``active`` (the postcondition of
          workflow §3.9, pinned by
          ``test_workflow_start_architect_session``).
        * A :class:`DesignDocument` exists for the target module
          (§3.11 precondition line 2: "``design_documents`` existuje
          pre daný modul"). In the happy path the row is already
          approved (``approved_by != NULL``) — it is the §3.5
          postcondition output that §3.11 picks up mid-implementation
          when a missing entity is discovered.

    Steps (per BEHAVIOR.md §3.11):
        1. Tibor in the Architect session writes "Pridaj tabuľku
           ``stock_adjustments`` do DESIGN.md pre STK" — the user
           turn is persisted via
           ``POST /api/v1/architect-messages`` with ``role='user'``
           and the Architect's structured reply (the proposed SQL
           block plus the insertion-point prose) is persisted via a
           second ``POST /api/v1/architect-messages`` with
           ``role='assistant'`` plus token / cost accounting (the
           §3.9 step 5 contract, reused verbatim here).
        2. Tibor approves the extension — the orchestrator PATCHes
           the existing ``design_documents`` row with the merged
           ``content`` (original + Architect's insertion) and the
           bumped ``version`` (``version + 1``). The router's
           ``DesignDocViewer`` surfaces the diff in the UI
           (client-side only — not HTTP-observable beyond the new
           ``content`` and ``version`` on the response).
        3. — (system) — the orchestrator writes the updated
           DESIGN.md to KB filesystem "ak ``file_path`` existuje".
           At the HTTP layer the observable is the module's
           ``design_doc_path`` remaining populated (the §3.5
           postcondition output is preserved; the extension does not
           unset it).

    Postcondition (per BEHAVIOR.md §3.11):
        * The ``design_documents`` row has a new version with the
          added content — ``content`` now contains the
          ``stock_adjustments`` schema in addition to the original
          ``stock_items`` and ``warehouses`` tables.
        * ``version`` is incremented (1 → 2) — monotonic per the
          D-03 version-history convention.
        * The filesystem copy is updated — observed at the HTTP
          layer as ``design_doc_path`` remaining populated on the
          module row. The actual filesystem write is orchestration
          territory and is not asserted here.
        * Approval stamps are preserved — ``approved_by`` and
          ``approved_at`` are *not* re-stamped by an extension. This
          mirrors the §3.5 "approval is sticky" contract pinned by
          ``test_workflow_approve_design_md``
          (``test_approval_is_sticky_on_subsequent_patch``): once a
          DESIGN.md is approved, content edits and version bumps do
          not re-stamp the original approval. Without this guard the
          §3.10 import gate would think a fresh review is pending
          every time the Architect extends the doc.

Edge cases verified alongside the happy path:

    * **Repeated extensions stack monotonically** — a second
      extension (e.g. "Pridaj tabuľku ``stock_movements``") on top
      of the first must bump ``version`` from 2 to 3, preserve the
      original ``approved_at`` stamp, and leave the merged
      ``content`` containing *all three* extensions in order. This
      is the §3.11 "ad-hoc počas vývoja" frequency in action — a
      single session may trigger the workflow several times as new
      entities are discovered. Pins the ``version`` ``MAX + 1``
      allocation at the HTTP surface rather than relying on an
      untested server-side auto-increment.
    * **Extension preserves the original approval stamp** — the
      orchestrator is deliberately *not* a `PATCH
      approved_by=<tibor>` call. ``approved_by`` is *sticky once
      set* at the service layer
      (:func:`backend.services.design_document.update` auto-stamps
      only on ``None → user_id`` transitions), but an extension
      payload that omits ``approved_by`` entirely also preserves
      the stamp — the "fields left out of the PATCH are left
      unchanged" PATCH semantics §3.5 pins. This pins that the §3.5
      monotonic-approval contract survives the §3.11 in-place
      update path, not just the no-op post-approval PATCH the §3.5
      edge test covers.
    * **Extending a non-existent DESIGN.md returns 404** — a stale
      browser tab or a direct API hit with a wrong UUID must not
      corrupt the database. The router's ``_map_value_error``
      translates the service-layer ``ValueError("... not found")``
      into HTTP 404 and rolls back the session — no row is created
      / touched. Documents a direction not exercised on the happy
      path; costs nothing and catches a whole class of regressions
      at the ``document_id`` lookup.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri, member of project, Architect session active"
    precondition is satisfied by persisting the actor with the
    correct ``role`` and seeding the active session row. Role
    enforcement at the router level is a separate concern covered by
    future auth-middleware tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.specifications import DesignDocument

# ---------------------------------------------------------------------------
# Precondition fixtures — Tibor (ri_senior), the NEX Horizont project,
# the STK module in ``in_development`` (post-§3.5 transition) with an
# approved DESIGN.md and a populated ``design_doc_path``, and an active
# Architect session scoped to STK.
# ---------------------------------------------------------------------------


@pytest.fixture()
def tibor(db_session) -> User:
    """Persist Tibor — the ``ri_senior`` primary actor in §3.11's worked example.

    BEHAVIOR.md §3.11 names Tibor in the Steps table ("Tibor v session
    napíše …"). Zoltán (``ri_director``) is an equally valid approver
    per the Actor line — both resolve to ``role='ri'`` at the DB
    level — but the worked example is Tibor's and the happy-path
    test follows suit.
    """
    user = User(
        username="tibor",
        email="tibor@isnex.ai",
        password_hash="hashed-placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def nex_horizont(db_session, tibor) -> Project:
    """Persist the NEX Horizont project with Tibor as member.

    Matches the §3.11 worked example — the DESIGN.md being extended
    belongs to the STK module of NEX Horizont. ``created_by`` is
    Tibor because his role is ``ri`` (the row's FK requires a user);
    authorship is not material to §3.11.
    """
    project = Project(
        name="NEX Horizont",
        slug="nex-horizont",
        category="multimodule",
        description="Enterprise ERP successor to NEX Command.",
        created_by=tibor.id,
    )
    db_session.add(project)
    db_session.flush()

    return project


# The KB filesystem path the orchestrator records on the module after
# the DESIGN.md is written to disk, per BEHAVIOR.md §3.5 step 4
# ("zapíše dokument do ``/home/icc/knowledge/projects/<project>/modules/<module>/DESIGN.md``").
# §3.11 step 3 writes the *updated* DESIGN.md to the same path — the
# path itself is immutable across extensions, only the file content
# changes on disk.
STK_DESIGN_MD_KB_PATH = "/home/icc/knowledge/projects/nex-horizont/modules/STK/DESIGN.md"


@pytest.fixture()
def stk_in_development(db_session, nex_horizont) -> ProjectModule:
    """Persist STK in ``in_development`` with a populated ``design_doc_path``.

    BEHAVIOR.md §3.11 fires "ad-hoc počas vývoja" — during
    implementation. The precondition is therefore the post-§3.5
    module state: ``status='in_development'`` (approval has already
    flipped the gate open, assuming dependencies were satisfied) and
    ``design_doc_path`` populated (the KB file exists). The extension
    updates the file in place rather than creating a new path.

    Modelling STK here instead of re-using the §3.5 DOB fixture keeps
    the test's worked example aligned with §3.11's own text, which
    names STK in step 1 verbatim.
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="stk",
        name="Skladové karty zásob",
        category="Sklad",
        status="in_development",
        design_doc_path=STK_DESIGN_MD_KB_PATH,
    )
    db_session.add(module)
    db_session.flush()
    return module


# The original DESIGN.md content — the §3.5 postcondition output that
# §3.11 picks up. Two tables (``stock_items``, ``warehouses``) and a
# placeholder-level "Data model" section; the extension in step 1 of
# the happy-path test adds a third table (``stock_adjustments``) to
# the same section.
STK_DESIGN_MD_V1 = (
    "# DESIGN.md — modul STK (Skladové karty zásob)\n\n"
    "## 1. Data model\n"
    "### 1.1 `stock_items`\n"
    "| column | type | notes |\n"
    "|--------|------|-------|\n"
    "| id     | UUID PK | gen_random_uuid() |\n"
    "| sku    | varchar(64) | unique per warehouse |\n"
    "### 1.2 `warehouses`\n"
    "| column | type | notes |\n"
    "|--------|------|-------|\n"
    "| id     | UUID PK | gen_random_uuid() |\n"
    "| code   | varchar(10) | unique |\n"
)

# The §3.11 worked-example extension — the ``stock_adjustments`` table
# Tibor discovers he needs while wiring up the service layer. Appended
# to the original DESIGN.md as a new subsection ``### 1.3``. The exact
# markdown structure is orchestration territory; the test only
# verifies the merged ``content`` contains the extension and the
# original material survives intact.
STOCK_ADJUSTMENTS_EXTENSION = (
    "### 1.3 `stock_adjustments`\n"
    "| column        | type          | notes                                    |\n"
    "|---------------|---------------|------------------------------------------|\n"
    "| id            | UUID PK       | gen_random_uuid()                        |\n"
    "| stock_item_id | UUID FK       | → `stock_items.id` ON DELETE RESTRICT   |\n"
    "| delta         | numeric(12,2) | signed adjustment quantity               |\n"
    "| reason        | varchar(40)   | `inventory_count|damage|correction|…`    |\n"
    "| created_at    | timestamptz   | server_default now()                     |\n"
)

STK_DESIGN_MD_V2 = STK_DESIGN_MD_V1 + STOCK_ADJUSTMENTS_EXTENSION

# The edge-case second extension — a ``stock_movements`` table added on
# top of the v2 content. Pins the monotonic ``version`` bump across
# repeated extensions and the "all prior content survives" contract.
STOCK_MOVEMENTS_EXTENSION = (
    "### 1.4 `stock_movements`\n"
    "| column            | type          | notes                               |\n"
    "|-------------------|---------------|-------------------------------------|\n"
    "| id                | UUID PK       | gen_random_uuid()                   |\n"
    "| from_warehouse_id | UUID FK       | → `warehouses.id` nullable          |\n"
    "| to_warehouse_id   | UUID FK       | → `warehouses.id` nullable          |\n"
    "| stock_item_id     | UUID FK       | → `stock_items.id`                  |\n"
    "| quantity          | numeric(12,2) | must be positive                    |\n"
)

STK_DESIGN_MD_V3 = STK_DESIGN_MD_V2 + STOCK_MOVEMENTS_EXTENSION


@pytest.fixture()
def approved_stk_design(db_session, nex_horizont, stk_in_development, tibor) -> DesignDocument:
    """Persist an **approved** DESIGN.md (v1) for the STK module.

    §3.11 precondition line 2: "``design_documents`` existuje pre
    daný modul". In the happy path the row is already approved (the
    §3.5 postcondition) — the extension is an in-place update to the
    approved row, not a new draft. ``approved_by`` / ``approved_at``
    are populated so the §3.11 test can assert they survive the
    extension unchanged.
    """
    doc = DesignDocument(
        project_id=nex_horizont.id,
        module_id=stk_in_development.id,
        doc_type="design",
        content=STK_DESIGN_MD_V1,
        version=1,
        approved_by=tibor.id,
        approved_at=datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc),
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def active_stk_session(db_session, nex_horizont, stk_in_development, tibor) -> ArchitectSession:
    """Persist an **active** Architect session scoped to STK.

    §3.11 precondition line 1: "Architect session je ``active``".
    This row is the postcondition of workflow §3.9 (pinned by
    ``test_workflow_start_architect_session``); §3.11 picks it up
    unchanged and uses it to carry the two conversation turns that
    drive the extension.
    """
    session = ArchitectSession(
        project_id=nex_horizont.id,
        module_id=stk_in_development.id,
        created_by=tibor.id,
        status="active",
    )
    db_session.add(session)
    db_session.flush()
    return session


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.11 end-to-end.
# ---------------------------------------------------------------------------


class TestExtendDesignMdHappyPath:
    """End-to-end walkthrough of workflow §3.11 against the real app."""

    def test_extension_bumps_version_and_preserves_approval(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_development,
        approved_stk_design,
        active_stk_session,
    ):
        """Drive steps 1-3 of the workflow and verify every postcondition.

        Reproduces the §3.11 worked example faithfully: Tibor asks
        the Architect to add ``stock_adjustments`` to the STK
        DESIGN.md, the Architect proposes the schema, Tibor approves,
        and the orchestrator persists the merged ``content`` with
        ``version=2`` while preserving the original approval stamps.
        """
        session_id = str(active_stk_session.id)
        document_id = str(approved_stk_design.id)
        original_approved_at = approved_stk_design.approved_at

        # --- Step 0 (precondition recap): the approved DESIGN.md is
        # visible via the ``approved_by``-filtered list — the same
        # signal the §3.10 import gate and the UI's "DESIGN.md je
        # schválený" badge rely on.
        precond_list = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_development.id),
                "doc_type": "design",
                "approved_by": str(tibor.id),
            },
        )
        assert precond_list.status_code == 200, precond_list.text
        precond_rows = precond_list.json()["items"]
        assert len(precond_rows) == 1
        assert precond_rows[0]["id"] == document_id
        assert precond_rows[0]["version"] == 1
        assert precond_rows[0]["approved_by"] == str(tibor.id)

        # --- Step 0 (precondition recap): the active Architect session
        # is visible via the module-scoped filter §3.9 pins.
        precond_sessions = client.get(
            "/api/v1/architect-sessions",
            params={
                "module_id": str(stk_in_development.id),
                "status": "active",
            },
        )
        assert precond_sessions.status_code == 200
        assert precond_sessions.json()["total"] == 1
        assert precond_sessions.json()["items"][0]["id"] == session_id

        # --- Step 1 (Tibor): the user prompt turn — the §3.11 worked
        # example verbatim.
        user_prompt = "Pridaj tabuľku `stock_adjustments` do DESIGN.md pre STK"
        user_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "user",
                "content": user_prompt,
            },
        )
        assert user_msg_resp.status_code == 201, user_msg_resp.text
        user_msg = user_msg_resp.json()
        assert user_msg["role"] == "user"
        assert user_msg["content"] == user_prompt
        assert user_msg["session_id"] == session_id

        # --- Step 1 (system): the Architect proposes the SQL schema
        # and the insertion point in DESIGN.md. Token / cost
        # accounting mirrors the §3.9 step-5 contract.
        assistant_reply = (
            "Navrhujem pridať tabuľku `stock_adjustments` pod sekciu "
            "## 1. Data model ako ### 1.3.\n\n"
            "```sql\nCREATE TABLE stock_adjustments (\n"
            "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n"
            "  stock_item_id UUID NOT NULL REFERENCES stock_items (id)\n"
            "    ON DELETE RESTRICT,\n"
            "  delta NUMERIC(12, 2) NOT NULL,\n"
            "  reason VARCHAR(40) NOT NULL,\n"
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()\n"
            ");\n```\n\n"
            "Vloží sa za existujúce ### 1.2 warehouses.\n"
        )
        assistant_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "assistant",
                "content": assistant_reply,
                "input_tokens": 2048,
                "output_tokens": 420,
                "cost_usd": "0.041280",
            },
        )
        assert assistant_msg_resp.status_code == 201, assistant_msg_resp.text
        assistant_msg_id = assistant_msg_resp.json()["id"]

        # --- Step 2 (Tibor): "Tibor schváli rozšírenie". The
        # orchestrator PATCHes the existing DESIGN.md row with the
        # merged ``content`` and the bumped ``version``. Crucially
        # ``approved_by`` is *not* re-sent — the PATCH leaves the
        # approval stamps untouched per the "fields omitted from the
        # payload are left unchanged" semantics.
        extend_resp = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={
                "content": STK_DESIGN_MD_V2,
                "version": 2,
            },
        )
        assert extend_resp.status_code == 200, extend_resp.text
        extended = extend_resp.json()
        # §3.11 postcondition line 1: the row now carries the new
        # content with the stock_adjustments extension merged in.
        assert extended["content"] == STK_DESIGN_MD_V2
        assert "stock_adjustments" in extended["content"]
        # Original material survives — the extension is additive,
        # not a replacement.
        assert "stock_items" in extended["content"]
        assert "warehouses" in extended["content"]
        # §3.11 postcondition line 2: ``version`` incremented.
        assert extended["version"] == 2
        # §3.11 postcondition line 4 (cross-cut from §3.5 monotonic
        # approval): approval stamps preserved.
        assert extended["approved_by"] == str(tibor.id)
        assert extended["approved_at"] is not None
        # ``updated_at`` has advanced (ORM ``onupdate=func.now()``)
        # but ``approved_at`` is the *original* stamp, not ``now()``.
        assert datetime.fromisoformat(extended["approved_at"].replace("Z", "+00:00")) == original_approved_at
        # Immutable columns stayed put.
        assert extended["id"] == document_id
        assert extended["project_id"] == str(nex_horizont.id)
        assert extended["module_id"] == str(stk_in_development.id)
        assert extended["doc_type"] == "design"

        # --- Step 3 (system): the orchestrator writes the updated
        # DESIGN.md to filesystem "ak ``file_path`` existuje". At
        # the HTTP layer the observable is that the module's
        # ``design_doc_path`` is *still* populated — an extension
        # does not clear the path even though the file's *contents*
        # on disk have changed.
        module_after = client.get(f"/api/v1/project-modules/{stk_in_development.id}")
        assert module_after.status_code == 200
        assert module_after.json()["design_doc_path"] == STK_DESIGN_MD_KB_PATH

        # --- Postcondition verification (HTTP) ------------------------
        # Re-read the DESIGN.md through the GET endpoint — mirrors
        # the next refresh the UI / Architect context injection does.
        after_doc = client.get(f"/api/v1/design-documents/{document_id}")
        assert after_doc.status_code == 200
        assert after_doc.json()["content"] == STK_DESIGN_MD_V2
        assert after_doc.json()["version"] == 2
        assert after_doc.json()["approved_by"] == str(tibor.id)
        assert after_doc.json()["approved_at"] == extended["approved_at"]

        # The conversation that drove the extension is persisted in
        # ``architect_messages`` in order — the "show the Architect
        # reply that produced this DESIGN.md version" link the UI
        # offers.
        transcript_resp = client.get(
            "/api/v1/architect-messages",
            params={"session_id": session_id},
        )
        assert transcript_resp.status_code == 200
        assert transcript_resp.json()["total"] == 2
        assert [row["role"] for row in transcript_resp.json()["items"]] == ["user", "assistant"]

        # --- Postcondition verification (DB state) --------------------
        db_session.expire_all()

        # 1. ``design_documents`` row has the merged content and the
        #    bumped version.
        persisted_doc = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted_doc is not None
        assert persisted_doc.content == STK_DESIGN_MD_V2
        assert persisted_doc.version == 2
        # 2. Approval stamps preserved — §3.5 monotonic contract
        #    survives §3.11.
        assert persisted_doc.approved_by == tibor.id
        assert persisted_doc.approved_at == original_approved_at
        # 3. Immutable columns still hold — §3.11 touches content /
        #    version only, never the identity columns.
        assert persisted_doc.project_id == nex_horizont.id
        assert persisted_doc.module_id == stk_in_development.id
        assert persisted_doc.doc_type == "design"
        # 4. ``updated_at`` is at least as new as ``created_at`` —
        #    the ORM ``onupdate=func.now()`` fires on every flush.
        #    (We cannot compare against a Python-side ``datetime.now``
        #    reading because SAVEPOINT-isolated tests share a single
        #    connection-level transaction and Postgres ``now()`` is
        #    pinned to the transaction start, which is *before* the
        #    test entry point — so a wall-clock comparison would be
        #    a flaky false negative.)
        assert persisted_doc.updated_at >= persisted_doc.created_at

        # 5. The module's ``design_doc_path`` is still populated —
        #    step 3 postcondition at the DB level.
        persisted_module = db_session.get(ProjectModule, stk_in_development.id)
        assert persisted_module is not None
        assert persisted_module.design_doc_path == STK_DESIGN_MD_KB_PATH

        # 6. The assistant message that drove the extension is still
        #    addressable — the transcript link the UI shows under
        #    the version-history entry.
        persisted_assistant = db_session.get(
            ArchitectMessage,
            uuid.UUID(assistant_msg_id),
        )
        assert persisted_assistant is not None
        assert persisted_assistant.role == "assistant"
        assert persisted_assistant.session_id == active_stk_session.id

        # 7. The approved-by-filtered list still surfaces the row —
        #    the §3.10 import gate and the UI "DESIGN.md je schválený"
        #    badge remain green across the extension.
        post_extension_list = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_development.id),
                "doc_type": "design",
                "approved_by": str(tibor.id),
            },
        )
        assert post_extension_list.status_code == 200
        assert [row["id"] for row in post_extension_list.json()["items"]] == [document_id]
        assert post_extension_list.json()["items"][0]["version"] == 2


# ---------------------------------------------------------------------------
# Edge cases — repeated extensions, approval stickiness, 404 on fabricated id.
# ---------------------------------------------------------------------------


class TestExtendDesignMdEdgeCases:
    """Edge cases for the ``extend_design_md`` workflow.

    BEHAVIOR.md §4 does not list a dedicated ``§4.X`` edge under
    ``extend_design_md`` itself, but three cross-cutting contracts
    apply at this boundary and are worth pinning:

    1. The "ad-hoc počas vývoja" frequency implies **repeated
       extensions within a session** — ``version`` must bump
       monotonically (1 → 2 → 3 → …) and each extension must preserve
       the prior accumulated content.
    2. Approval is **monotonic / sticky** (§3.5) — an extension PATCH
       that omits ``approved_by`` / ``approved_at`` leaves both
       unchanged. Pinned here independently of the §3.5 test because
       §3.11 uses the same code path with a *different* payload
       shape (content + version, no approval columns).
    3. A PATCH on a fabricated ``document_id`` returns HTTP 404 with
       no DB side-effect — a general service-layer contract, reused
       at every router, pinned at the §3.11 boundary so a stale
       browser tab does not silently corrupt the DESIGN.md corpus.
    """

    def test_repeated_extensions_bump_version_monotonically(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_development,
        approved_stk_design,
        active_stk_session,
    ):
        """Two extensions in a row bump version 1 → 2 → 3 and stack content.

        Models the "ad-hoc počas vývoja" §3.11 frequency: Tibor
        extends the DESIGN.md for ``stock_adjustments`` (the worked
        example), discovers a few lines later that he also needs
        ``stock_movements``, and triggers the workflow a second
        time. Both extensions must land as monotonic version bumps
        (1 → 2 → 3) and the merged ``content`` must contain *all
        three* tables (original ``stock_items`` / ``warehouses``,
        plus both extensions) — no regressions, no silent drops.
        """
        document_id = str(approved_stk_design.id)
        original_approved_at = approved_stk_design.approved_at

        # --- Extension #1: stock_adjustments (the worked example).
        first = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={
                "content": STK_DESIGN_MD_V2,
                "version": 2,
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["version"] == 2
        assert "stock_adjustments" in first.json()["content"]
        # Approval stamp still the original — the extension does not
        # re-stamp it.
        assert datetime.fromisoformat(first.json()["approved_at"].replace("Z", "+00:00")) == original_approved_at

        # --- Extension #2: stock_movements layered on top of the v2
        # content. The orchestrator reads the current row, appends
        # the new extension, and PATCHes with version=3.
        second = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={
                "content": STK_DESIGN_MD_V3,
                "version": 3,
            },
        )
        assert second.status_code == 200, second.text
        assert second.json()["version"] == 3
        # All three extensions merged — ``stock_items`` /
        # ``warehouses`` (v1), ``stock_adjustments`` (v2) and
        # ``stock_movements`` (v3) all present.
        final_content = second.json()["content"]
        assert "stock_items" in final_content
        assert "warehouses" in final_content
        assert "stock_adjustments" in final_content
        assert "stock_movements" in final_content
        # Original approval stamp still intact after both extensions.
        assert second.json()["approved_by"] == str(tibor.id)
        assert datetime.fromisoformat(second.json()["approved_at"].replace("Z", "+00:00")) == original_approved_at

        # --- DB verification: monotonic version, single row (§3.11
        # updates in place; it does not create a new row per version).
        db_session.expire_all()
        persisted = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted is not None
        assert persisted.version == 3
        assert persisted.content == STK_DESIGN_MD_V3
        assert persisted.approved_by == tibor.id
        assert persisted.approved_at == original_approved_at

        # --- HTTP list still returns exactly one row for the module
        # — §3.11 is an in-place update, not a version-history row
        # insert. Distinct from the D-03 pattern where versions *are*
        # separate rows; §3.11's worked example is specifically
        # "aktualizuje ``design_documents.content``".
        list_resp = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_development.id),
                "doc_type": "design",
            },
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1
        assert list_resp.json()["items"][0]["id"] == document_id
        assert list_resp.json()["items"][0]["version"] == 3

    def test_extension_patch_without_approval_columns_preserves_approval(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_development,
        approved_stk_design,
        active_stk_session,
    ):
        """Extension PATCH that omits ``approved_by`` leaves the stamp intact.

        The §3.5 monotonic-approval contract is keyed on
        ``document.approved_by is None`` at service entry, so a
        post-approval PATCH that sends ``approved_by`` again is a
        no-op. §3.11 uses a *different* payload shape — content /
        version only, no approval columns at all — and the PATCH
        semantics ("fields left out are left unchanged") must
        preserve both ``approved_by`` and ``approved_at``. Without
        this guard the §3.10 import gate would flip closed on every
        extension and the UI would show a spurious "needs re-review"
        badge.
        """
        document_id = str(approved_stk_design.id)
        original_approved_at = approved_stk_design.approved_at

        # PATCH without any approval column — only ``content`` /
        # ``version``, as the §3.11 orchestrator would send.
        resp = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={
                "content": STK_DESIGN_MD_V2,
                "version": 2,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Approval untouched — the whole point of the test.
        assert body["approved_by"] == str(tibor.id)
        assert datetime.fromisoformat(body["approved_at"].replace("Z", "+00:00")) == original_approved_at

        # DB agrees.
        db_session.expire_all()
        persisted = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted is not None
        assert persisted.approved_by == tibor.id
        assert persisted.approved_at == original_approved_at

    def test_extending_nonexistent_document_returns_404(
        self,
        client,
        db_session,
        tibor,
    ):
        """PATCH on a fabricated UUID → 404, no DB side-effect.

        A stale browser tab or a mis-routed API hit with a wrong
        UUID must not corrupt the database. The router's
        ``_map_value_error`` translates the service-layer
        ``ValueError("... not found")`` into HTTP 404 and rolls back
        the session — no row is created / touched. Pinned at the
        §3.11 boundary to catch regressions in the shared 404
        machinery of :mod:`backend.api.routes.design_documents`.
        """
        fabricated = uuid.uuid4()

        resp = client.patch(
            f"/api/v1/design-documents/{fabricated}",
            json={
                "content": STK_DESIGN_MD_V2,
                "version": 2,
            },
        )
        assert resp.status_code == 404, resp.text
        assert "not found" in resp.json()["detail"].lower()

        db_session.expire_all()
        assert db_session.get(DesignDocument, fabricated) is None
