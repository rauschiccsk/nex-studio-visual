"""DEV-156 — code nobody uses does not reach a customer: Verifikácia of every version of every project stops on dead
code the version added; what was there before is offered as a cleanup.

Director 20.10.2026, after the cockpit's own deep check (DEV-155): „Chcem aby takáto kontrola bola zabudovaná do
kokpitu.“ 23.10.2026 to the plan and its rules: „Áno, schvaľujem všetkých 10 bodov“. The check reads real git trees with
the real vulture; the TypeScript half (knip and the calls of the routes) runs in a sealed container in production and is
played here by its report.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.api.routes import dead_code as dead_code_routes
from backend.core.security import get_current_user
from backend.main import app
from backend.services import dead_code, orchestrator
from tests.test_a_version_gets_its_orientacna_cena_after_navrh import _owner, _project
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_pass,
)
from tests.test_the_code_a_customer_gets_is_english import _guide


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@t", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@t",
             "PATH": "/usr/bin:/bin"},
    ).stdout.strip()  # fmt: skip


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for path, text in files.items():
        target = repo / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    _git(path, "init", "-q")
    return path


BASE = {
    "backend/app/main.py": (
        "from fastapi import APIRouter, FastAPI\n"
        "from starlette.middleware.base import BaseHTTPMiddleware\n"
        "\n"
        "from app.money import total\n"
        "\n"
        'router = APIRouter(prefix="/invoices")\n'
        "\n"
        "\n"
        '@router.get("/{invoice_id}")\n'
        "def read_invoice(invoice_id: int):\n"
        "    return total([invoice_id])\n"
        "\n"
        "\n"
        "class Origin(BaseHTTPMiddleware):\n"
        "    async def dispatch(self, request, call_next):\n"
        "        return await call_next(request)\n"
        "\n"
        "\n"
        "app = FastAPI()\n"
        "app.add_middleware(Origin)\n"
        "app.include_router(router)\n"
    ),
    "backend/app/money.py": ("def total(lines):\n    return sum(lines)\n\n\ndef forgotten_before():\n    return 0\n"),
}


def _sentences(findings: list[dead_code.Finding]) -> list[str]:
    return [f.sentence() for f in findings]


def test_only_what_the_version_added_stops_it(repo):
    base = _commit(repo, BASE, "base")
    head = _commit(
        repo,
        {
            "backend/app/tax.py": (
                "def vat(amount):\n    return amount * 0.2\n\n\ndef only_for_tests():\n    return 1\n"
            ),
            "backend/app/money.py": BASE["backend/app/money.py"] + "\n\ndef added_and_forgotten():\n    return 2\n",
            "backend/app/main.py": BASE["backend/app/main.py"] + "\nfrom app.tax import vat\n\nRATE = vat(1)\n",
            "backend/tests/test_tax.py": (
                "from app.tax import only_for_tests\n\n\ndef test_it():\n    assert only_for_tests()\n"
            ),
        },
        "version",
    )

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == [
        "funkciu added_and_forgotten (backend/app/money.py:9) nikto nevolá",
        "funkciu only_for_tests (backend/app/tax.py:5) nikto nevolá",
    ]
    assert _sentences(report.old) == ["funkciu forgotten_before (backend/app/money.py:5) nikto nevolá"]
    assert report.not_run == []


def test_a_version_that_removes_the_last_caller_made_that_code_dead(repo):
    base = _commit(repo, BASE, "base")
    main = BASE["backend/app/main.py"].replace("    return total([invoice_id])", "    return invoice_id")
    head = _commit(repo, {"backend/app/main.py": main.replace("from app.money import total\n", "")}, "version")

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == ["funkciu total (backend/app/money.py:1) nikto nevolá"]


def test_without_a_beginning_everything_is_the_cleanup(repo):
    head = _commit(repo, BASE, "base")

    report = dead_code.scan(repo, None, head)

    assert report.new == []
    assert _sentences(report.old) == ["funkciu forgotten_before (backend/app/money.py:5) nikto nevolá"]


def test_what_a_framework_calls_is_not_dead(tmp_path):
    files = {
        "app/api.py": (
            "from pydantic import BaseModel, field_validator\n"
            "\n"
            "\n"
            "class Invoice(BaseModel):\n"
            "    number: str\n"
            "\n"
            '    @field_validator("number")\n'
            "    @classmethod\n"
            "    def strip(cls, value):\n"
            "        return value.strip()\n"
            "\n"
            "\n"
            '@invoices_router.post("/")\n'
            "def create(body: Invoice):\n"
            "    return body\n"
            "\n"
            "\n"
            "def upgrade():\n"
            "    pass\n"
            "\n"
            "\n"
            "class Handler:\n"
            "    def do_GET(self):\n"
            "        pass\n"
            "\n"
            "    def emit(self, record):\n"
            "        pass\n"
            "\n"
            "\n"
            "def nobody():\n"
            "    pass\n"
        ),
        "app/__init__.py": "from app.api import nobody\n\n__all__ = ['nobody']\n",
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")

    found = dead_code.python_findings(tmp_path, sorted(tmp_path.rglob("*.py")))

    # Handler is a class nobody names — vulture rightly says so; what a library calls on it is not judged.
    assert [(f.kind, f.name) for f in found] == [("class", "Handler"), ("function", "nobody")]


def test_a_function_the_projects_own_decorator_registers_is_not_dead(tmp_path):
    """NEX Inbox registers its release assertions with ``@feature(...)`` — on 1.9.0 five of them would have stopped the
    version. A decorator that only wraps (``staticmethod``, ``lru_cache``) leaves a function judged."""
    (tmp_path / "checks.py").write_text(
        "from app.registry import feature\n"
        "import functools\n"
        "\n"
        "\n"
        '@feature("mailbox outage is announced")\n'
        "def mailbox_outage_is_announced():\n"
        "    pass\n"
        "\n"
        "\n"
        "@functools.lru_cache\n"
        "def cached_and_forgotten():\n"
        "    pass\n"
        "\n"
        "\n"
        "class Box:\n"
        "    @staticmethod\n"
        "    def unused_static():\n"
        "        pass\n",
        encoding="utf-8",
    )

    found = dead_code.python_findings(tmp_path, [tmp_path / "checks.py"])

    assert [(f.kind, f.name) for f in found] == [
        ("function", "cached_and_forgotten"),
        ("class", "Box"),
        ("method", "Box.unused_static"),
    ]


def test_two_methods_of_one_name_are_two_findings(repo):
    base = _commit(
        repo,
        {
            "app/jobs.py": (
                "class Mail:\n    def run(self):\n        pass\n\n\n"
                "class Pdf:\n    def go(self):\n        pass\n\n\n"
                "Mail(); Pdf().go()\n"
            )
        },
        "base",
    )
    head = _commit(
        repo,
        {
            "app/jobs.py": (
                "class Mail:\n    def run(self):\n        pass\n\n\n"
                "class Pdf:\n    def go(self):\n        pass\n\n    def run(self):\n        pass\n\n\n"
                "Mail(); Pdf().go()\n"
            )
        },
        "version",
    )

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == ["metódu Pdf.run (app/jobs.py:10) nikto nevolá"]
    assert _sentences(report.old) == ["metódu Mail.run (app/jobs.py:2) nikto nevolá"]


def test_a_file_the_version_moved_brings_no_new_dead_code(repo):
    base = _commit(repo, {"app/util.py": "def old_dead():\n    pass\n", "app/main.py": "x = 1\n"}, "base")
    _git(repo, "mv", "app/util.py", "app/helpers.py")
    head = _commit(repo, {}, "move")

    report = dead_code.scan(repo, base, head)

    assert report.new == []
    assert _sentences(report.old) == ["funkciu old_dead (app/helpers.py:1) nikto nevolá"]


def test_the_tree_is_read_as_git_stores_it(repo):
    """An attribute of the tree cannot hide a file (``export-ignore`` would from ``git archive``), and a link is never
    followed — not even one that points out of the project."""
    base = _commit(repo, BASE, "base")
    (repo / "app").mkdir()
    (repo / "app" / "outside.py").symlink_to("/etc/hostname")
    head = _commit(
        repo,
        {
            "app/extra.py": "def hidden_and_forgotten():\n    pass\n",
            ".gitattributes": "app/extra.py export-ignore\n",
        },
        "version",
    )

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == ["funkciu hidden_and_forgotten (app/extra.py:1) nikto nevolá"]
    assert report.not_run == []


def test_a_backend_that_calls_its_own_service_over_http_calls_that_route(tmp_path):
    (tmp_path / "gate.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.post("/gate/open")\n'
        "def open_gate(): ...\n"
        '@router.post("/gate/close")\n'
        "def close_gate(): ...\n",
        encoding="utf-8",
    )
    (tmp_path / "client.py").write_text(
        'import httpx\n\ndef ask(base):\n    return httpx.post(f"{base}/gate/open")\n', encoding="utf-8"
    )
    files = [tmp_path / "gate.py", tmp_path / "client.py"]

    found = dead_code.orphan_routes(dead_code.routes(tmp_path, files), dead_code.python_calls(files))

    # the path in a route's own decorator ("/gate/close") is its declaration, not a call of it
    assert _sentences(found) == ["trasu POST /gate/close (gate.py:5) nevolá žiadna obrazovka"]


# --- routes ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "call", "reached"),
    [
        ("/invoices/{invoice_id}", "/api/v1/invoices/{}", True),  # the call carries the include prefix
        ("/invoices/{invoice_id}", "/invoices/{}", True),
        ("/invoices/{invoice_id}/files", "{}/files", True),  # a helper's tail
        ("/invoices/{invoice_id}", "/invoices", False),
        ("/invoices/{invoice_id}", "/suppliers/{}", False),
        ("/invoices", "/api/v1/invoices", True),
        ("/invoices/{invoice_id}", "/invoices/42", False),  # a fixed segment is not a parameter's value
        ("/files", "{}/x/files", False),  # a tail longer than the route
        ("/reports", "/api/v1/reports{}", True),  # a query glued to the path (`/reports${qs}`)
    ],
)
def test_a_call_reaches_the_route_it_names(route, call, reached):
    assert dead_code.reaches(route, call) is reached


def test_a_route_no_screen_calls_is_found_by_its_method_too(tmp_path):
    (tmp_path / "api.py").write_text(
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/invoices")\n'
        '@router.get("/{invoice_id}")\n'
        "def read(invoice_id): ...\n"
        '@router.delete("/{invoice_id}")\n'
        "def remove(invoice_id): ...\n"
        '@router.get("/{invoice_id}/pdf")\n'
        "def pdf(invoice_id): ...\n"
        '@router.get("/{anything}")\n'
        "def nothing_fixed_to_judge(anything): ...\n",
        encoding="utf-8",
    )
    calls = [{"method": "GET", "path": "/api/v1/invoices/{}"}, {"method": "*", "path": "{}/pdf"}]

    found = dead_code.orphan_routes(dead_code.routes(tmp_path, [tmp_path / "api.py"]), calls)

    assert _sentences(found) == ["trasu DELETE /invoices/{invoice_id} (api.py:5) nevolá žiadna obrazovka"]


def test_a_route_with_nothing_fixed_in_its_path_is_not_judged(tmp_path):
    """``/{slug}`` of a router without a prefix would look reached by any call — and unreached by none; not judged."""
    (tmp_path / "api.py").write_text('@app.get("/{slug}")\ndef page(slug): ...\n', encoding="utf-8")

    found = dead_code.routes(tmp_path, [tmp_path / "api.py"])

    assert [(r.method, r.path) for r in found] == [("GET", "/{slug}")]
    assert dead_code.orphan_routes(found, [{"method": "GET", "path": "/api/v1/invoices"}]) == []


# --- the TypeScript half ---------------------------------------------------------------------------------------------


FRONTEND_BASE = {
    "frontend/package.json": '{"name": "web"}\n',
    "frontend/src/main.tsx": "export {};\n",
}


def _half(issues_at: dict[str, list], calls_at: dict[str, list], errors: dict[str, str] | None = None):
    """The TypeScript half's report, per tree, for the one package ``frontend``."""

    def run(_repo, trees, packages):
        assert packages == ["frontend"]
        out = {}
        for tree in trees:
            result = {"issues": issues_at.get(tree, []), "calls": calls_at.get(tree, [])}
            if errors and tree in errors:
                result = {"error": errors[tree], "calls": result["calls"]}
            out[tree] = {"frontend": result}
        return out

    return run


EXPORT = {"file": "src/lib.ts", "exports": [{"name": "unusedHelper", "line": 3}]}
ROUTE_CALL = [{"method": "GET", "path": "/api/v1/invoices/{}"}]


def test_the_frontend_and_the_routes_are_judged_by_what_the_version_added(repo, monkeypatch):
    base = _commit(repo, {**BASE, **FRONTEND_BASE}, "base")
    head = _commit(
        repo,
        {
            "backend/app/extra.py": (
                "from fastapi import APIRouter\n"
                'router = APIRouter(prefix="/reports")\n'
                '@router.get("/monthly")\n'
                "def monthly(): ...\n"
                '@router.get("/daily")\n'
                "def daily(): ...\n"
            ),
            # the backend's own job calls the daily report over HTTP — that route is called
            "backend/app/job.py": 'import httpx\n\nhttpx.get("http://localhost/api/v1/reports/daily")\n',
        },
        "version",
    )
    monkeypatch.setattr(
        dead_code,
        "_run_script_half",
        _half(
            {"base": [], "head": [EXPORT]},
            {"base": ROUTE_CALL, "head": ROUTE_CALL},
        ),
    )

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == [
        "trasu GET /reports/monthly (backend/app/extra.py:3) nevolá žiadna obrazovka",
        "export unusedHelper zo súboru frontend/src/lib.ts:3 nikto nepoužíva",
    ]
    # The invoice route is called; what was dead before stays the cleanup.
    assert _sentences(report.old) == ["funkciu forgotten_before (backend/app/money.py:5) nikto nevolá"]


def test_a_frontend_that_could_not_be_checked_never_stops_a_version_on_a_guess(repo, monkeypatch):
    base = _commit(repo, {**BASE, **FRONTEND_BASE}, "base")
    head = _commit(repo, {"frontend/src/main.tsx": "export {};\n// touched\n"}, "version")
    monkeypatch.setattr(
        dead_code,
        "_run_script_half",
        _half({"head": [EXPORT]}, {"base": ROUTE_CALL, "head": ROUTE_CALL}, errors={"base": "knip zlyhal"}),
    )

    report = dead_code.scan(repo, base, head)

    assert report.new == []
    assert _sentences(report.old)[-1] == "export unusedHelper zo súboru frontend/src/lib.ts:3 nikto nepoužíva"


def test_a_container_that_cannot_run_is_said_and_python_is_still_judged(repo, monkeypatch):
    base = _commit(repo, {**BASE, **FRONTEND_BASE}, "base")
    head = _commit(repo, {"backend/app/money.py": BASE["backend/app/money.py"] + "\n\ndef late():\n    pass\n"}, "v")

    def _refused(*_a):
        raise dead_code.CheckNotRun("kontajner kontroly frontendu sa nedal spustiť (no docker)")

    monkeypatch.setattr(dead_code, "_run_script_half", _refused)

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == ["funkciu late (backend/app/money.py:9) nikto nevolá"]
    assert report.not_run == [
        "frontend a trasy sa neskontrolovali — kontajner kontroly frontendu sa nedal spustiť (no docker)"
    ]


def test_the_typescript_half_runs_sealed(tmp_path, monkeypatch):
    """knip runs the project's configuration — foreign code. Its container sees no network, no Docker of the server,
    nothing it could write, and the project only read-only."""
    head = tmp_path / "work" / "head"
    (head / "frontend").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / "frontend" / "node_modules").mkdir(parents=True)
    seen: list[list[str]] = []

    def _run(argv, **_kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"head": {}}', stderr="")

    monkeypatch.setattr(dead_code.subprocess, "run", _run)

    assert dead_code._run_script_half(repo, {"head": head}, ["frontend"]) == {"head": {}}
    argv = seen[0]
    pairs = list(zip(argv, argv[1:], strict=False))
    assert ("--pull", "never") in pairs  # never an image of the same name from a public registry
    assert ("--network", "none") in pairs
    assert "--read-only" in argv
    assert ("--cap-drop", "ALL") in pairs
    assert ("--user", "65534:65534") in pairs  # a user that owns nothing on the host
    assert argv[argv.index("--name") + 1].startswith("nsv-dead-code-")
    mounts = [value for flag, value in pairs if flag == "-v"]
    assert mounts == [
        f"{head}:/work/head:ro",
        f"{repo.resolve()}/frontend/node_modules:/work/head/frontend/node_modules:ro",
    ]
    assert not any("docker.sock" in a for a in argv)


def test_installed_packages_are_shown_only_from_inside_the_project(tmp_path, monkeypatch):
    """The checkout is the agent's to write: a ``node_modules`` that leads elsewhere on the server — the home with the
    credentials, another project — must never reach the container (a bind mount follows a link)."""
    head = tmp_path / "work" / "head"
    for pkg in ("frontend", "web", "admin"):
        (head / pkg).mkdir(parents=True)
    repo = tmp_path / "repo"
    secrets = tmp_path / "home" / ".claude"
    secrets.mkdir(parents=True)
    (repo / "frontend").mkdir(parents=True)
    (repo / "frontend" / "node_modules").symlink_to(secrets)
    (repo / "web" / "node_modules").mkdir(parents=True)
    (repo / "web" / "node_modules" / "real").mkdir()
    (repo / "admin").mkdir()
    (repo / "admin" / "node_modules").symlink_to(repo / "web" / "node_modules")  # inside, but a link all the same
    seen: list[list[str]] = []

    def _run(argv, **_kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(dead_code.subprocess, "run", _run)

    dead_code._run_script_half(repo, {"head": head}, ["admin", "frontend", "web"])

    pairs = list(zip(seen[0], seen[0][1:], strict=False))
    assert [value for flag, value in pairs if flag == "-v"] == [
        f"{head}:/work/head:ro",
        f"{(repo / 'web' / 'node_modules').resolve()}:/work/head/web/node_modules:ro",
    ]


def test_what_the_projects_code_says_on_its_error_stream_never_reaches_a_sentence(tmp_path, monkeypatch, caplog):
    head = tmp_path / "head"
    head.mkdir()

    def _run(argv, **_kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="TOKEN=leaked-by-vite-config")

    monkeypatch.setattr(dead_code.subprocess, "run", _run)

    with pytest.raises(dead_code.CheckNotRun) as refused:
        dead_code._run_script_half(tmp_path, {"head": head}, [])

    assert str(refused.value) == "kontajner kontroly frontendu skončil kódom 1"


def test_a_container_past_its_time_is_removed_by_its_name(tmp_path, monkeypatch):
    head = tmp_path / "head"
    head.mkdir()
    seen: list[list[str]] = []

    def _run(argv, **kw):
        seen.append(argv)
        if argv[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(dead_code.subprocess, "run", _run)

    with pytest.raises(dead_code.CheckNotRun, match=f"nedobehla do {dead_code.SCRIPT_CAP} s"):
        dead_code._run_script_half(tmp_path, {"head": head}, [])

    name = seen[0][seen[0].index("--name") + 1]
    assert seen[1] == ["docker", "rm", "-f", "-v", name]


# --- the exceptions --------------------------------------------------------------------------------------------------


def test_an_exception_with_its_reason_lets_the_code_be_and_the_manager_reads_it(repo):
    base = _commit(repo, BASE, "base")
    head = _commit(
        repo,
        {
            "backend/app/jobs.py": "def nightly():\n    pass\n\n\ndef forgotten():\n    pass\n",
            dead_code.EXCEPTIONS_FILE: (
                "- path: backend/app/jobs.py\n"
                "  name: nightly\n"
                "  reason: Called by the server's cron at 02:00, not by the code.\n"
                "- path: backend/app/jobs.py\n"
                "  name: forgotten\n"
            ),
        },
        "version",
    )

    report = dead_code.scan(repo, base, head)

    assert _sentences(report.new) == ["funkciu forgotten (backend/app/jobs.py:5) nikto nevolá"]
    assert [w.sentence() for w in report.waivers_added] == [
        "nightly (backend/app/jobs.py) — Called by the server's cron at 02:00, not by the code"
    ]
    assert report.invalid_waivers == [
        f"výnimka forgotten (backend/app/jobs.py) v {dead_code.EXCEPTIONS_FILE} nemá dôvod (reason) — neplatí"
    ]


def test_an_exceptions_file_that_is_not_a_list_excepts_nothing(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / dead_code.EXCEPTIONS_FILE).write_text("nightly: cron\n", encoding="utf-8")

    assert dead_code.waivers(tmp_path) == (
        [],
        [f"{dead_code.EXCEPTIONS_FILE} nie je zoznam výnimiek — žiadna výnimka neplatí"],
    )


# --- Verifikácia -----------------------------------------------------------------------------------------------------


def _project_repo(monkeypatch, tmp_path, project, files: dict[str, str]) -> None:
    root = tmp_path / "projects"
    path = root / project.slug
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    base = _commit(path, BASE, "base")
    _commit(path, files, "version")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(orchestrator.delivery_statement, "base_plan", lambda db, version: [("sha", base)])


async def test_dead_code_floors_the_auditors_pass_and_reaches_the_fixer(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _project_repo(
        monkeypatch,
        tmp_path,
        project,
        {"backend/app/tax.py": "def vat(amount):\n    return amount * 0.2\n", **_guide(version)},
    )
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)

    async def _no_critic(*_a, **_k):
        raise AssertionError("a FAIL only the cockpit measured has no Auditor fix scope to vet")

    monkeypatch.setattr(orchestrator, "_run_fix_critique", _no_critic)

    await orchestrator.run_dispatch(db_session, version.id)

    verdict = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    finding = {
        "text": (
            "Verzia pridala kód, ktorý nikto nepoužíva (1): funkciu vat (backend/app/tax.py:1) nikto nevolá. Odstráň "
            "ho. Export, ktorý potrebuje len skúška, označ komentárom /** @internal */. Čo predsa volá niečo mimo kódu "
            "projektu (rámec, plánovač, iný systém), nechaj a zapíš výnimku s dôvodom do "
            f"{dead_code.EXCEPTIONS_FILE} (položky path, name, reason)."
        ),
        "blocking": True,
        "escapes_tests": False,
    }
    assert verdict.payload["verdict"] == "FAIL"
    assert verdict.payload["engine_override"] == "dead_code"
    assert verdict.payload["findings"][-1] == finding
    assert verdict.content.startswith("Verzia funguje, ale pridala kód, ktorý nikto nepoužíva — treba ho odstrániť.")
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert f"- {finding['text']}" in orchestrator._latest_verifikacia_fix_scope(db_session, version.id)


async def test_a_version_without_dead_code_passes_and_the_manager_reads_its_exceptions(
    db_session, monkeypatch, tmp_path
):
    version, project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _project_repo(
        monkeypatch,
        tmp_path,
        project,
        {
            "backend/app/jobs.py": "def nightly():\n    pass\n",
            dead_code.EXCEPTIONS_FILE: (
                "- path: backend/app/jobs.py\n  name: nightly\n  reason: Called by the server's cron at 02:00.\n"
            ),
            **_guide(version),
        },
    )
    monkeypatch.setattr(orchestrator, "_commit_release_note", AsyncMock(return_value=None))
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_push_release_artifacts", lambda *a, **k: None)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)

    await orchestrator.run_dispatch(db_session, version.id)

    verdict = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    assert orchestrator._verifikacia_passed(db_session, version.id) is True
    assert verdict.content.endswith(
        " Kontrola nepoužívaného kódu — výnimky pridané v tejto verzii (preverí ich Audítor): nightly "
        "(backend/app/jobs.py) — Called by the server's cron at 02:00."
    )


async def test_a_failure_of_the_check_itself_never_takes_verifikacia_down(db_session, monkeypatch):
    version, _project = _make_version(db_session)

    async def _boom(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(orchestrator, "run_blocking", _boom)

    assert await orchestrator._dead_code_check(db_session, version.id, "x") == (
        None,
        ["Kontrola nepoužívaného kódu neprebehla — v kokpite nastala chyba (je zapísaná v jeho logu)."],
    )


async def test_a_check_that_cannot_run_never_stops_a_release_and_says_why(db_session, monkeypatch):
    version, _project = _make_version(db_session)

    async def _boom(*_a, **_k):
        raise dead_code.CheckNotRun("zmena abc12345 sa nedá prečítať z gitu (broken)")

    monkeypatch.setattr(orchestrator, "run_blocking", _boom)

    assert await orchestrator._dead_code_check(db_session, version.id, "x") == (
        None,
        ["Kontrola nepoužívaného kódu neprebehla — zmena abc12345 sa nedá prečítať z gitu (broken)."],
    )


def test_the_charters_name_the_exceptions_file_the_check_reads():
    """The agent writes the exceptions where the check reads them, and the Auditor reviews them there."""
    templates = Path(__file__).resolve().parents[1] / "templates"
    for charter in ("ai-agent-charter.md", "auditor-charter.md"):
        text = (templates / charter).read_text(encoding="utf-8")
        assert f"`{dead_code.EXCEPTIONS_FILE}`" in text, charter
        assert "DEV-156" in text, charter


# --- the cleanup on the project page ---------------------------------------------------------------------------------


@pytest.fixture
def project_repo(client, db_session, monkeypatch, tmp_path):
    owner = _owner(db_session)
    project = _project(db_session, owner, "Inbox")
    db_session.commit()
    root = tmp_path / "projects"
    repo = root / project.slug
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _commit(repo, BASE, "base")
    monkeypatch.setattr(dead_code_routes.claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(dead_code_routes, "_results", {})
    app.dependency_overrides[get_current_user] = lambda: owner
    return project, repo


def _counting_scans(monkeypatch) -> list[str]:
    scans: list[str] = []
    real = dead_code.scan

    def _scan(repo, base, head):
        scans.append(head)
        return real(repo, base, head)

    monkeypatch.setattr(dead_code, "scan", _scan)
    return scans


def test_the_project_page_offers_the_cleanup_of_what_earlier_versions_left(client, project_repo, monkeypatch):
    project, repo = project_repo
    scans = _counting_scans(monkeypatch)

    first = client.get(f"/api/v1/projects/{project.slug}/dead-code")
    again = client.get(f"/api/v1/projects/{project.slug}/dead-code")

    assert first.status_code == 200
    assert first.json() == {
        "findings": [
            {
                "kind": "function",
                "path": "backend/app/money.py",
                "name": "forgotten_before",
                "line": 5,
                "sentence": "funkciu forgotten_before (backend/app/money.py:5) nikto nevolá",
            }
        ],
        "not_run": [],
        "brief": (
            "Uprac kód, ktorý nikto nepoužíva — kontrola kokpitu ho našla v projekte z minulých verzií (1):\n"
            "- funkciu forgotten_before (backend/app/money.py:5) nikto nevolá\n"
            "Každú z týchto vecí odstráň. Export, ktorý potrebuje len skúška, označ komentárom /** @internal */. Čo "
            "predsa volá niečo mimo kódu projektu (rámec, plánovač, iný systém), nechaj a zapíš výnimku s dôvodom do "
            f"{dead_code.EXCEPTIONS_FILE} (položky path, name, reason)."
        ),
    }
    assert again.json() == first.json()
    assert len(scans) == 1  # kept for the commit

    _commit(repo, {"backend/app/money.py": "def total(lines):\n    return sum(lines)\n"}, "cleanup")
    cleaned = client.get(f"/api/v1/projects/{project.slug}/dead-code")

    assert cleaned.json() == {"findings": [], "not_run": [], "brief": None}
    assert len(scans) == 2  # a new commit is checked anew


def test_a_check_that_did_not_run_is_said_and_asked_again(client, project_repo, monkeypatch):
    project, _repo = project_repo
    outcomes = iter([dead_code.CheckNotRun("dočasný priečinok chýba"), dead_code.Report()])

    def _scan(*_a):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(dead_code, "scan", _scan)

    now = [1000.0]
    monkeypatch.setattr(dead_code_routes.time, "monotonic", lambda: now[0])

    first = client.get(f"/api/v1/projects/{project.slug}/dead-code")
    soon = client.get(f"/api/v1/projects/{project.slug}/dead-code")
    now[0] += dead_code_routes.RETRY_AFTER + 1
    later = client.get(f"/api/v1/projects/{project.slug}/dead-code")

    assert first.json() == {"findings": [], "not_run": ["dočasný priečinok chýba"], "brief": None}
    assert soon.json() == first.json()  # a broken check is not run again on every visit
    assert later.json() == {"findings": [], "not_run": [], "brief": None}


def test_someone_who_may_not_work_on_the_project_sees_no_cleanup(client, project_repo, db_session):
    project, _repo = project_repo
    stranger = _owner(db_session)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: stranger

    response = client.get(f"/api/v1/projects/{project.slug}/dead-code")

    assert response.status_code in (403, 404)
