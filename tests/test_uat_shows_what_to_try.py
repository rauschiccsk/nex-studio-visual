"""DEV-90 — the UAT tab shows what to try in a new version; the agent writes the guide, verification checks it.

12.10.2026 the Director: „V záložke UAT by som privýtal súhrn toho čo mám otestovať v novej verzii.“ He dug it out
of the documentation. The agent now adds steps to the version's UAT test guide in every task that changes what a
user sees (the rule rides each task's brief), verification does not pass without the guide, and the UAT tab reads
the guide of the verified commit — or, for an older version, the changes of its release note.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from backend.db.models.customers import Customer
from backend.db.models.pipeline import PipelineState
from backend.db.models.tasks import Task
from backend.db.models.versions import Version
from backend.services import orchestrator, uat_guide
from tests.test_delivered_tokens_statement import _as, _git, _write, project  # noqa: F401 — the fixture is used by name
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_pass,
)
from tests.test_the_code_a_customer_gets_is_english import _project_repo

GUIDE = (
    "# Návod na skúšku v UAT\n\n"
    "## Detail faktúry v Chrome 109\n"
    "Obrazovka: Faktúry → detail\n"
    "Testovacie údaje: faktúra EMOS\n"
    "1. Otvor zoznam faktúr.\n"
    "2. Klikni na faktúru EMOS.\n"
    "Očakávané: Detail sa otvorí aj s PDF.\n\n"
    "## Objednávkový kód z ISDOC\n"
    "1. Otvor faktúru RMJ.\n"
    "Očakávané: Pri položkách je objednávkový kód.\n"
)


# ── the shape ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_guide_reads_into_changes_with_steps_and_what_has_to_happen():
    guide = uat_guide.parse(GUIDE)

    assert [(s.title, s.screen, s.data, s.steps, s.expected) for s in guide.sections] == [
        (
            "Detail faktúry v Chrome 109",
            "Faktúry → detail",
            "faktúra EMOS",
            ["Otvor zoznam faktúr.", "Klikni na faktúru EMOS."],
            ["Detail sa otvorí aj s PDF."],
        ),
        ("Objednávkový kód z ISDOC", None, None, ["Otvor faktúru RMJ."], ["Pri položkách je objednávkový kód."]),
    ]
    assert uat_guide.problems(GUIDE) == []


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (None, ["chýba"]),
        ("# Návod\n\nNiečo.\n", ["nemá ani jednu zmenu (## nadpis) ani riadok „Nie je čo skúšať: …“"]),
        ("## Export\nOčakávané: XML vznikne.\n", ["zmena „Export“ nemá kroky"]),
        ("## Export\n1. Klikni Export.\n", ["zmena „Export“ nemá „Očakávané: …“"]),
        ("## Export\n", ["zmena „Export“ nemá kroky ani „Očakávané: …“"]),
        ("Nie je čo skúšať: oprava len v skúškach CI.\n", []),
    ],
)
def test_a_guide_without_steps_or_an_outcome_is_not_one_to_test_from(text, found):
    assert uat_guide.problems(text) == found


def test_the_guide_lies_beside_the_release_notes_of_the_version():
    assert uat_guide.relative_path("1.8.0") == "docs/specs/versions/v1.8.0/UAT_SKUSKA.md"


# ── the brief ───────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("flow", ["new_version", "fast_fix"])
def test_every_task_brief_tells_the_agent_to_write_the_guide(flow):
    task = Task(number=1, title="Detail faktúry", description="Oprav detail.", task_type="frontend")

    brief = orchestrator._directive_for_build_task(task, None, [], flow, task_label="1.1.1", version_number="1.8.0")

    assert uat_guide.BRIEF_RULE.format(path="docs/specs/versions/v1.8.0/UAT_SKUSKA.md") in brief


def test_every_call_of_the_task_brief_passes_the_version():
    """A rule, not a list: every place that builds a task brief hands it the version, so no brief goes out without
    the guide's path."""
    tree = ast.parse(inspect.getsource(orchestrator))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_directive_for_build_task"
    ]
    assert calls, "no call of _directive_for_build_task found — the guard would pass on nothing"
    assert all(any(k.arg == "version_number" for k in call.keywords) for call in calls)


# ── verification ─────────────────────────────────────────────────────────────────────────────────────────────────


def _verify(db_session, monkeypatch, tmp_path, files):
    version, app = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _project_repo(monkeypatch, tmp_path, app, {"backend/app/screen.py": "SHOWN = True\n", **files(version)})
    monkeypatch.setattr(orchestrator, "_commit_release_note", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_push_release_artifacts", lambda *a, **k: None)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    return version


async def test_without_the_guide_the_auditors_pass_is_floored_and_the_fixer_gets_why(db_session, monkeypatch, tmp_path):
    async def _no_critic(*_a, **_k):
        raise AssertionError("a FAIL only the cockpit measured has no Auditor fix scope to vet")

    monkeypatch.setattr(orchestrator, "_run_fix_critique", _no_critic)
    version = _verify(db_session, monkeypatch, tmp_path, lambda v: {})

    await orchestrator.run_dispatch(db_session, version.id)

    verdict = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    finding = verdict.payload["findings"][-1]
    assert verdict.payload["verdict"] == "FAIL"
    assert verdict.payload["engine_override"] == "uat_guide"
    assert finding["blocking"] is True
    assert finding["text"].startswith(
        f"Návod na skúšku v UAT ({uat_guide.relative_path(version.version_number)}) chýba"
    )
    assert verdict.content.startswith("Verzia funguje, ale chýba úplný návod na skúšku v UAT")
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert f"- {finding['text']}" in orchestrator._latest_verifikacia_fix_scope(db_session, version.id)


async def test_an_incomplete_guide_says_which_change_misses_what(db_session, monkeypatch, tmp_path):
    version = _verify(
        db_session,
        monkeypatch,
        tmp_path,
        lambda v: {uat_guide.relative_path(v.version_number): "## Export\n1. Klikni Export.\n"},
    )

    await orchestrator.run_dispatch(db_session, version.id)

    verdict = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    assert verdict.payload["verdict"] == "FAIL"
    assert "nie je úplný: zmena „Export“ nemá „Očakávané: …“" in verdict.payload["findings"][-1]["text"]


@pytest.mark.parametrize("text", [GUIDE, "Nie je čo skúšať: oprava len v skúškach CI.\n"])
async def test_a_guide_to_test_from_lets_the_version_pass(db_session, monkeypatch, tmp_path, text):
    version = _verify(db_session, monkeypatch, tmp_path, lambda v: {uat_guide.relative_path(v.version_number): text})

    await orchestrator.run_dispatch(db_session, version.id)

    assert orchestrator._verifikacia_passed(db_session, version.id) is True


def test_a_project_without_a_checkout_is_never_stopped_on_a_guess(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "bez-kopie").mkdir()

    assert orchestrator._uat_guide_finding("bez-kopie", "1.0.0") is None
    assert orchestrator._uat_guide_finding("ziadny-priecinok", "1.0.0") is None


# ── the UAT tab ─────────────────────────────────────────────────────────────────────────────────────────────────


def _verified(db, proj_id, number, sha):
    v = Version(project_id=proj_id, version_number=number, name=number, status="active")
    db.add(v)
    db.flush()
    db.add(
        PipelineState(
            version_id=v.id,
            flow_type="new_version",
            current_stage="done",
            current_actor="auditor",
            status="done",
            next_action="",
        )
    )
    orchestrator._record_message(
        db,
        version_id=v.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia", "verified_sha": sha},
    )
    db.flush()
    return v


def test_the_uat_tab_reads_the_guide_of_the_verified_commit_or_the_release_notes(client, project):  # noqa: F811
    db, root, owner, v010, _v011, _d010, _d011 = project
    _write(root, uat_guide.relative_path("0.2.0"), GUIDE)
    _write(root, ".env.example", "# @customer required: „E-mail prvého správcu“\nBOOTSTRAP_ADMIN_EMAIL=\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: 0.2.0")
    _verified(db, v010.project_id, "0.2.0", _git(root, "rev-parse", "HEAD").strip())
    # the guide written after the verified commit is not the version's — the UAT tab shows what was verified
    _write(root, uat_guide.relative_path("0.3.0"), GUIDE)
    _write(
        root,
        "docs/specs/versions/v0.3.0/RELEASE_NOTES.md",
        "## v0.3.0\n\n- **Export do CSV** pri zozname faktúr.\n- Rýchlejšie hľadanie.\n",
    )
    _git(root, "add", "docs/specs/versions/v0.3.0/RELEASE_NOTES.md")
    _git(root, "commit", "-q", "-m", "docs: 0.3.0 notes")
    _verified(db, v010.project_id, "0.3.0", _git(root, "rev-parse", "HEAD").strip())
    icc = Customer(
        project_id=v010.project_id, name="ICC s.r.o.", slug="icc", settings={"BOOTSTRAP_ADMIN_EMAIL": "z@icc.sk"}
    )
    bare = Customer(project_id=v010.project_id, name="Bez nastavení", slug="bez")
    db.add_all([icc, bare])
    db.flush()
    _as(db, owner)
    slug = root.name

    with_guide = client.get(f"/api/v1/projects/{slug}/uat-guide", params={"version": "0.2.0"}).json()
    from_notes = client.get(f"/api/v1/projects/{slug}/uat-guide", params={"version": "0.3.0"}).json()
    nothing = client.get(f"/api/v1/projects/{slug}/uat-guide", params={"version": "0.1.0"}).json()

    assert with_guide["source"] == "guide"
    assert [(s["title"], s["steps"], s["expected"]) for s in with_guide["sections"]][0] == (
        "Detail faktúry v Chrome 109",
        ["Otvor zoznam faktúr.", "Klikni na faktúru EMOS."],
        ["Detail sa otvorí aj s PDF."],
    )
    assert {x["customer_id"]: x["login"] for x in with_guide["logins"]} == {str(icc.id): "z@icc.sk", str(bare.id): None}
    assert (from_notes["source"], from_notes["changes"]) == (
        "release_notes",
        ["Export do CSV pri zozname faktúr.", "Rýchlejšie hľadanie."],
    )
    assert (nothing["source"], nothing["sections"], nothing["changes"]) == ("none", [], [])
    assert client.get(f"/api/v1/projects/{slug}/uat-guide", params={"version": "9.9.9"}).status_code == 404


def test_a_release_note_corrected_after_the_version_is_the_one_to_test_by(tmp_path):
    """DEV-97 — a version's note is read as the project has it now; the guide stays the verified commit's.

    Career Asistent 0.1.1 shipped its agent brief as the release note, and the UAT tab listed it as what to try.
    The corrected note lands on the main branch after the version; the verified commit keeps the old text."""
    import subprocess

    repo = tmp_path / "app"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t.sk")
    git("config", "user.name", "T")
    note = repo / "docs/specs/versions/v0.1.1/RELEASE_NOTES.md"
    note.parent.mkdir(parents=True)
    note.write_text("## v0.1.1\n\n- Mark in `.env.example` what each customer provides.\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "0.1.1")
    verified = git("rev-parse", "HEAD").strip()
    brief = ["Mark in `.env.example` what each customer provides."]
    assert uat_guide.for_commit(repo, "0.1.1", verified).changes == brief

    note.write_text("## v0.1.1\n\n- Pri nasadení zadáte e-mail prvého správcu.\n", encoding="utf-8")
    git("commit", "-q", "-am", "corrected note")
    assert uat_guide.for_commit(repo, "0.1.1", verified).changes == ["Pri nasadení zadáte e-mail prvého správcu."]

    guide = repo / uat_guide.relative_path("0.1.1")
    guide.write_text(GUIDE, encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "a guide after the version")
    assert uat_guide.for_commit(repo, "0.1.1", verified).source == uat_guide.RELEASE_NOTES

    git("rm", "-q", str(note.relative_to(repo)))
    git("commit", "-q", "-m", "note gone")
    assert uat_guide.for_commit(repo, "0.1.1", verified).changes == brief


def test_an_app_on_the_legacy_admin_password_signs_in_as_admin():
    assert uat_guide.admin_login([], {}, legacy_admin=True) == "admin"
    assert uat_guide.admin_login([], {}, legacy_admin=False) is None


def test_the_password_never_reaches_the_tab():
    class _Setting:
        def __init__(self, key):
            self.key = key

    settings = [_Setting("BOOTSTRAP_ADMIN_PASSWORD"), _Setting("BOOTSTRAP_ADMIN_EMAIL")]

    assert (
        uat_guide.admin_login(settings, {"BOOTSTRAP_ADMIN_PASSWORD": "not-shown", "BOOTSTRAP_ADMIN_EMAIL": ""}, False)
        is None
    )
