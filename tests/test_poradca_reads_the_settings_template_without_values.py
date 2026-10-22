"""DEV-177 — Poradca reads the app's settings template (``.env.example``) with names, comments and markers, never
values.

Every ``.env*`` was overlaid with an empty file in Poradca's container — the template too, although it is where an app
says who provides each setting (``@customer``, ``@customer-secret``, ``@public-url``, ``@public-url-admin``; DEV-72).
Reviewing the Špecifikácia of Career Asistent 1.1.0 on 21.10.2026 Poradca wrote it could not check the markers.
Director 21.10.2026: „Súhlasím“. The template is now overlaid with a copy that has no values — even one written into
the template by mistake does not reach Poradca. Every other ``.env*`` stays empty.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services import env_contract, secret_files
from backend.services.poradca import sandbox
from tests.test_poradca_runner import _ask, world  # noqa: F401 — fixture

TEMPLATE = """\
# Career Asistent — settings
# @customer-secret required: „Heslo prvého administrátora“
BOOTSTRAP_ADMIN_PASSWORD=hunter2
# @public-url
APP_PUBLIC_URL=https://demo.example.com

PRIVATE_KEY="-----BEGIN KEY-----
# not a comment, the middle of a value
-----END KEY-----"
export LEGACY_TOKEN=abc123
SMTP_HOST=smtp.example.com # a comment after a value is part of what is cut
"""

NAMES_ONLY = """\
# Career Asistent — settings
# @customer-secret required: „Heslo prvého administrátora“
BOOTSTRAP_ADMIN_PASSWORD=
# @public-url
APP_PUBLIC_URL=

PRIVATE_KEY=
SMTP_HOST=
"""

VALUES = ("hunter2", "demo.example.com", "BEGIN KEY", "middle of a value", "abc123", "smtp.example.com")


def test_the_copy_keeps_names_comments_and_markers_and_no_value():
    copy = secret_files.names_only(TEMPLATE)

    assert copy == NAMES_ONLY
    assert not [value for value in VALUES if value in copy]
    # Poradca reads the same contract the cockpit deploys by — who provides each setting, and the markers' problems.
    assert env_contract.parse(copy) == env_contract.parse(TEMPLATE)
    assert [s.key for s in env_contract.parse(copy).settings] == ["BOOTSTRAP_ADMIN_PASSWORD", "APP_PUBLIC_URL"]


def test_a_single_quoted_value_over_lines_is_cut_whole():
    assert secret_files.names_only("A='one\n# two\nthree'\nB=x\n") == "A=\nB=\n"


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    proj = root / "demo"
    proj.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "project_dirs", lambda slug: (str(root / slug), str(root / slug)))
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path / "poradca"))
    sandbox.run_dir("abc123").mkdir(parents=True)
    return proj


def _write(proj: Path, rel: str, text: str) -> None:
    (proj / rel).parent.mkdir(parents=True, exist_ok=True)
    (proj / rel).write_text(text, encoding="utf-8")


def _mount_sources(proj: Path, overlays: list[str], templates: dict[str, str]) -> dict[str, str]:
    argv = sandbox.run_argv(
        project_slug="demo",
        conversation_id=uuid4(),
        token="abc123",
        network="nex-poradca-demo-abc123",
        call=sandbox.ClaudeCall(
            prompt="Sú značky v poriadku?", claude_session_id=uuid4(), charter_text=None, model="opus", effort="high"
        ),
        overlays=overlays,
        templates=templates,
    )
    sources = {}
    for i, arg in enumerate(argv):
        if arg == "--mount" and argv[i + 1].startswith("type=bind,") and f"target={proj}/" in argv[i + 1]:
            fields = dict(part.split("=", 1) for part in argv[i + 1].split(",") if "=" in part)
            sources[fields["target"][len(str(proj)) + 1 :]] = fields["source"]
    return sources


def test_poradca_sees_each_template_names_only_and_every_other_secret_empty(project):
    _write(project, ".env.example", TEMPLATE)
    _write(project, "backend/.env.sample", "DATABASE_URL=postgresql://u:p@db/x\n")
    _write(project, ".env", "BOOTSTRAP_ADMIN_PASSWORD=real-secret\n")
    _write(project, "frontend/.env.local", "VITE_KEY=real\n")
    _write(project, "private/.env.example", "MANAGERS_FILE=x\n")  # DEV-44: what was attached stays unread
    outside = project.parent / "elsewhere.txt"
    outside.write_text("SECRET=far-away\n")
    os.link(outside, project / ".env.template")  # a second name for a file anywhere on the disk

    overlays = sandbox.overlay_paths(str(project))
    templates = sandbox.template_copies(str(project), overlays, "abc123")
    sources = _mount_sources(project, overlays, templates)

    empty = str(sandbox.empty_file())
    assert sorted(templates) == [".env.example", "backend/.env.sample"]
    assert Path(sources[".env.example"]).read_text(encoding="utf-8") == NAMES_ONLY
    assert Path(sources["backend/.env.sample"]).read_text(encoding="utf-8") == "DATABASE_URL=\n"
    assert {rel: sources[rel] for rel in (".env", "frontend/.env.local", "private/.env.example", ".env.template")} == {
        ".env": empty,
        "frontend/.env.local": empty,
        "private/.env.example": empty,
        ".env.template": empty,
    }


def test_a_template_too_big_to_be_a_template_stays_empty(project, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_TEMPLATE_BYTES", 10)
    _write(project, ".env.example", TEMPLATE)

    assert sandbox.template_copies(str(project), [".env.example"], "abc123") == {}


async def test_the_question_runs_with_the_copy(world, monkeypatch):  # noqa: F811
    host = Path(sandbox.project_dirs("demo")[1])
    _write(host, ".env.example", TEMPLATE)
    monkeypatch.setattr(sandbox, "overlay_paths", lambda d: [".env.example"])
    seen: list[dict] = []
    patched = sandbox.run_argv

    def _capture(**kw):
        seen.append({rel: Path(path).read_text(encoding="utf-8") for rel, path in kw.get("templates", {}).items()})
        return patched(**kw)

    monkeypatch.setattr(sandbox, "run_argv", _capture)

    await _ask(world, "Sú značky v poriadku?")

    assert seen == [{".env.example": NAMES_ONLY}]
