"""Nástroje Poradcu „zozadu" (ICCINT-167, návrh §4.2 a §4.3). Hodnoty sú umelé."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services.poradca import tools, uat_db
from backend.services.poradca.context import UatInstallation
from backend.services.poradca.mcp_server import ToolError

ROOT = Path(__file__).resolve().parents[1]


# ── tlačidlá ───────────────────────────────────────────────────────────────────


def test_every_action_the_frontend_knows_has_a_button_label():
    """Akcia, ktorú frontend pozná, musí mať meno tlačidla — inak by Poradca radil menom z kódu."""
    text = (ROOT / "frontend" / "src" / "services" / "api" / "pipeline.ts").read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("export type PipelineActionName")), None)
    assert start is not None, "zoznam akcií vo frontende sa nenašiel — skúška by nič nestrážila"
    actions: set[str] = set()
    for line in lines[start + 1 :]:
        m = re.match(r'\s*\|\s*"([a-z_]+)"(\s*;)?', line)
        if m:
            actions.add(m.group(1))
            if m.group(2):
                break  # posledná akcia zoznamu končí bodkočiarkou (komentáre ich majú tiež — preto po riadkoch)
    assert len(actions) >= 15
    assert actions - set(tools.ACTION_LABELS) == set()


# ── kontajnery ───────────────────────────────────────────────────────────────────


def _ps(*rows: dict) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def test_containers_are_only_the_projects_own():
    uat = UatInstallation("andros", "ANDROS", Path("/opt/uat/andros/nex-demo"))
    out = tools._project_containers(
        _ps(
            {"Names": "vizual-nex-demo", "State": "running", "Status": "Up", "Image": "v", "Labels": ""},
            {"Names": "nex-build-db-nex-demo-abc", "State": "running", "Status": "Up", "Image": "pg", "Labels": ""},
            {
                "Names": "uat-andros-demo-backend",
                "State": "running",
                "Status": "Up",
                "Image": "b",
                "Labels": "a=b,com.docker.compose.project.working_dir=/opt/uat/andros/nex-demo,x=y",
            },
            # PROD toho istého zákazníka — nesmie sa objaviť
            {
                "Names": "andros-demo-backend",
                "State": "running",
                "Status": "Up",
                "Image": "b",
                "Labels": "com.docker.compose.project.working_dir=/opt/customers/andros/nex-demo",
            },
            # iný projekt s podobným menom
            {"Names": "vizual-nex-demo2", "State": "running", "Status": "Up", "Image": "v", "Labels": ""},
            {"Names": "nex-studio-visual-prod-db-1", "State": "running", "Status": "Up", "Image": "pg", "Labels": ""},
        ),
        "nex-demo",
        [uat],
    )
    names = {r["name"]: r["kind"] for r in out}
    assert names == {
        "vizual-nex-demo": "náhľad Vizuálu",
        "nex-build-db-nex-demo-abc": "databáza stavby",
        "uat-andros-demo-backend": "UAT andros",
    }


# ── kroky agenta stavby ───────────────────────────────────────────────────────────


def test_agent_steps_show_tool_and_target_and_errors_but_no_file_content(tmp_path):
    transcript = tmp_path / "t.jsonl"
    entries = [
        {
            "timestamp": "2026-10-05T10:00:00Z",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "/opt/projects/p/app.py"}}
                ]
            },
        },
        {
            "timestamp": "2026-10-05T10:00:01Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "1", "content": "OBSAH-SUBORU-TAJNY"}]},
        },
        {
            "timestamp": "2026-10-05T10:01:00Z",
            "message": {
                "content": [{"type": "tool_use", "id": "2", "name": "Bash", "input": {"command": "pytest -q\nnext"}}]
            },
        },
        {
            "timestamp": "2026-10-05T10:01:30Z",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "2", "is_error": True, "content": "1 failed: test_login"}
                ]
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\nnie-json\n")
    out = tools._agent_steps(tmp_path, 50, "/opt/projects/p")
    assert "Read: app.py" in out
    assert "Bash: pytest -q" in out and "next" not in out
    assert "✗ chyba: 1 failed: test_login" in out
    assert "OBSAH-SUBORU-TAJNY" not in out


def test_agent_steps_without_a_transcript(tmp_path):
    assert "ešte nepracoval" in tools._agent_steps(tmp_path / "nie", 10, "/opt/projects/p")


# ── git ───────────────────────────────────────────────────────────────────────


def test_git_path_must_stay_in_the_project():
    t = tools.PoradcaTools(project_id=uuid4(), version_id=None, user_id=uuid4())
    assert t._relative_path("/opt/projects/p", "backend/app.py") == "backend/app.py"
    assert t._relative_path("/opt/projects/p", None) is None
    for bad in ("../q/secret", "/etc/passwd", "backend/../../q"):
        with pytest.raises(ToolError):
            t._relative_path("/opt/projects/p", bad)


@pytest.mark.parametrize("commit", ["--output=/tmp/x", "HEAD", "abc", "abcdefg; rm", "-p"])
async def test_git_commit_must_be_a_hash(commit):
    t = tools.PoradcaTools(project_id=uuid4(), version_id=None, user_id=uuid4())
    with pytest.raises(ToolError):
        await t.git_zmena({"commit": commit})


def test_int_arguments_are_bounded():
    assert tools._int_arg({"pocet": 10_000}, "pocet", 30, 100) == 100
    for bad in (0, -1, "5", True, 1.5):
        with pytest.raises(ToolError):
            tools._int_arg({"pocet": bad}, "pocet", 30, 100)


# ── databáza UAT ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "SELECT stav, count(*) FROM faktury GROUP BY stav",
        "  with x as (select 1) select * from x;",
        "EXPLAIN SELECT 1",
        "-- komentár\nSELECT 1",
    ],
)
def test_reading_queries_pass(query):
    assert uat_db.query_problem(query) is None


@pytest.mark.parametrize(
    "query,word",
    [
        ("UPDATE faktury SET suma = 0", "SELECT"),
        ("DELETE FROM faktury", "SELECT"),
        ("SELECT 1; DROP TABLE faktury", "jeden"),
        ("SELECT 1 \\! id", "lomku"),
        ("/* SELECT */ DROP TABLE x", "SELECT"),
        ("   ", "prázdny"),
    ],
)
def test_other_queries_are_refused_before_the_database(query, word):
    problem = uat_db.query_problem(query)
    assert problem and word in problem


def test_select_is_wrapped_with_a_row_cap_explain_is_not():
    assert uat_db._wrapped("SELECT a FROM t;").startswith("SELECT * FROM (\nSELECT a FROM t\n) AS poradca_dotaz LIMIT")
    assert uat_db._wrapped("EXPLAIN SELECT 1") == "EXPLAIN SELECT 1"


def test_role_sql_grants_columns_not_tables_and_refuses_foreign_access_extensions():
    sql = uat_db.role_setup_sql("demo")
    assert "GRANT SELECT (%I)" in sql
    assert "GRANT SELECT ON" not in sql
    assert "default_transaction_read_only = on" in sql
    assert "statement_timeout = '10s'" in sql
    assert "BASE TABLE" in sql  # pohľady nie — cez pohľad by sa dalo dostať k stĺpcu hesla
    assert "dblink" in sql and "postgres_fdw" in sql
    for secret in ("password_hash", "api_token", "jwt_secret", "refresh_token", "otp_seed", "private_key"):
        assert re.search(uat_db.SECRET_COLUMN_PATTERN, secret, re.I), secret
    for plain in ("username", "stav", "suma", "created_at"):
        assert not re.search(uat_db.SECRET_COLUMN_PATTERN, plain, re.I), plain
