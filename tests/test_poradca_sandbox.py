"""Kontajner Poradcu — zoznam pripojení JE záruka (ICCINT-167, návrh §4.1).

Skúšky čítajú hotový ``docker run`` a pýtajú sa, čo v ňom JE a čo v ňom NIE JE. Pridanie pripojenia,
ktoré by Poradcovi otvorilo cudzí priečinok, ich zčervená.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services.poradca import sandbox


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from backend.services import claude_agent

    root = tmp_path / "projects"
    proj = root / "demo"
    proj.mkdir(parents=True)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(
        sandbox,
        "project_dirs",
        lambda slug: (str(root / slug), str(root / slug)),
    )
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path / "poradca"))
    return proj


def _argv(overlays=None, first=True):
    call = sandbox.ClaudeCall(
        prompt="Prečo agent stojí?",
        claude_session_id=uuid4(),
        charter_text="charta" if first else None,
        model="opus",
        effort="high",
    )
    return sandbox.run_argv(
        project_slug="demo",
        conversation_id=uuid4(),
        token="abc123",
        network="nex-poradca-demo-abc123",
        call=call,
        overlays=overlays or [],
    )


def _mounts(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]


def test_claude_runs_restricted_with_only_read_tools(project):
    argv = _argv()
    entry = argv.index("--entrypoint")
    claude = argv[entry + 3 :]
    assert "--restricted" in claude
    assert claude[claude.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--strict-mcp-config" in claude
    assert claude[claude.index("--permission-mode") + 1] == "dontAsk"
    assert claude[claude.index("--allowedTools") + 1] == "mcp__poradca"
    assert "bypassPermissions" not in claude
    assert "--dangerously-skip-permissions" not in claude
    assert claude[-1] == "Prečo agent stojí?"


def test_first_question_founds_the_session_later_ones_resume_it(project):
    first = _argv(first=True)
    later = _argv(first=False)
    assert "--session-id" in first and "--append-system-prompt" in first and "--resume" not in first
    assert "--resume" in later and "--session-id" not in later and "--append-system-prompt" not in later


def test_container_is_unprivileged_read_only_and_capped(project):
    argv = _argv()
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert "--cap-drop=ALL" in argv
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in argv
    assert argv[argv.index("--network") + 1] == "nex-poradca-demo-abc123"
    assert "--memory" in argv and "--pids-limit" in argv


def test_project_is_mounted_read_only_and_nothing_else_is_reachable(project):
    mounts = _mounts(_argv())
    project_mount = [m for m in mounts if f"target={project}," in m + ","]
    assert len(project_mount) == 1 and project_mount[0].endswith(",readonly")
    joined = "\n".join(mounts)
    for forbidden in (
        "docker.sock",
        "/opt/customers",
        "/opt/uat",
        "/opt/infra",
        "/home/icc/knowledge",
        "credentials",
        "source=/home/andros/.claude,",
    ):
        assert forbidden not in joined, forbidden
    # Záznam rozhovoru, nie priečinok agenta stavby.
    session = [m for m in mounts if "/.claude/projects/" in m]
    assert len(session) == 1 and "/poradca/sessions/" in session[0]


def test_secret_files_are_overlaid_with_the_empty_file(project):
    mounts = _mounts(_argv(overlays=[".env", "backend/.env.local"]))
    empty = str(sandbox.empty_file())
    assert f"type=bind,source={empty},target={project}/.env,readonly" in mounts
    assert f"type=bind,source={empty},target={project}/backend/.env.local,readonly" in mounts


def test_git_dir_is_hidden_behind_an_empty_tmpfs(project):
    (project / ".git").mkdir()
    mounts = _mounts(_argv())
    assert any(m.startswith(f"type=tmpfs,destination={project}/.git") for m in mounts)


def test_only_the_oauth_token_reaches_the_container_and_by_name(project):
    argv = _argv()
    env = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in env  # menom — hodnota nejde do argv
    assert not any(e.startswith("CLAUDE_CODE_OAUTH_TOKEN=") for e in env)
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "DATABASE_URL", "SECRET_KEY", "DEDO_API_TOKEN"):
        assert not any(e.split("=", 1)[0] == name for e in env), name


def test_unsafe_slug_is_refused():
    with pytest.raises(sandbox.PoradcaUnavailable):
        sandbox.project_dirs("..")


def test_overlay_finds_secret_files_and_hard_links_everywhere_but_git(tmp_path):
    proj = tmp_path / "p"
    (proj / "backend").mkdir(parents=True)
    (proj / "node_modules" / "x").mkdir(parents=True)
    (proj / ".git").mkdir()
    for rel in (
        ".env",
        "backend/.env.production",
        "node_modules/x/.env",
        "certs/server.pem",
        "keys/id_ed25519",
        "keys/id_ed25519.pub",
        "README.md",
        ".env.example",
        ".git/config",
    ):
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("fake")
    os.link(outside, proj / "innocent.txt")  # pevný odkaz na súbor mimo projektu
    os.symlink("/etc/hostname", proj / "odkaz.txt")  # symbolický odkaz rieši obmedzený režim (zmerané)

    found = set(sandbox.overlay_paths(str(proj)))
    assert {
        ".env",
        "backend/.env.production",
        "node_modules/x/.env",
        "certs/server.pem",
        "keys/id_ed25519",
        ".env.example",
        "innocent.txt",
    } <= found
    assert "keys/id_ed25519.pub" not in found
    assert "README.md" not in found
    assert "odkaz.txt" not in found
    assert not any(p.startswith(".git/") for p in found)


def test_too_many_overlays_fail_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_OVERLAYS", 2)
    proj = tmp_path / "p"
    proj.mkdir()
    for i in range(4):
        (proj / f".env.{i}").write_text("x")
    with pytest.raises(sandbox.PoradcaUnavailable):
        sandbox.overlay_paths(str(proj))


def test_prepare_refuses_without_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path / "chyba"))
    with pytest.raises(sandbox.PoradcaUnavailable, match="compose"):
        sandbox.prepare(uuid4(), "tok")


def test_shim_path_is_where_the_image_has_the_backend():
    # Obraz kopíruje backend do /app/backend (koreňový Dockerfile) — prostredník musí byť tam.
    rel = Path(sandbox.SHIM_PATH).relative_to("/app")
    assert (Path(__file__).resolve().parents[1] / rel).is_file()


# ── nález nezávislej previerky 05.10.2026: meno súboru nesmie vpašovať voľby do --mount ─────────────


@pytest.mark.parametrize(
    "name",
    [
        ".env.x,source=etc",
        '.env.q"uote',
        ".env.new\nline",
    ],
)
def test_a_secret_file_whose_name_could_inject_mount_options_is_refused(tmp_path, name):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / name).write_text("x")
    with pytest.raises(sandbox.PoradcaUnavailable, match="Premenuj"):
        sandbox.overlay_paths(str(proj))


def test_a_hard_linked_file_with_a_comma_is_refused_too(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    os.link(outside, proj / "nevinny,source=etc")
    with pytest.raises(sandbox.PoradcaUnavailable):
        sandbox.overlay_paths(str(proj))


def test_run_argv_itself_refuses_an_unsafe_overlay(project):
    with pytest.raises(sandbox.PoradcaUnavailable):
        _argv(overlays=[".env,source=/etc"])


def test_a_container_that_outlives_the_backend_is_swept_at_startup(project):
    # ``build_db.reap_orphans`` hľadá ``label=<OWNER_LABEL>`` — kontajner Poradcu musí niesť presne ten kľúč.
    from backend.services import build_db

    argv = _argv()
    labels = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
    assert [lbl.split("=", 1)[0] for lbl in labels] == [build_db.OWNER_LABEL]


def test_trash_moves_the_whole_record_in_one_step_and_can_put_it_back(project):
    cid = uuid4()
    assert sandbox.move_to_trash(cid) is None  # rozhovor bez záznamu
    record = sandbox.session_dir(cid)
    (record / "sub").mkdir(parents=True)
    (record / "sub" / "a.jsonl").write_text("x")
    trashed = sandbox.move_to_trash(cid)
    # DEV-52: the trash entry holds the record and the conversation's images, each under its own name.
    assert trashed is not None and not record.exists() and (trashed / "session" / "sub" / "a.jsonl").exists()
    sandbox.restore_from_trash(trashed, cid)
    assert (record / "sub" / "a.jsonl").read_text() == "x" and not trashed.exists()


def test_what_discard_cannot_remove_the_startup_sweep_does(project, monkeypatch):
    cid = uuid4()
    sandbox.session_dir(cid).mkdir(parents=True)
    trashed = sandbox.move_to_trash(cid)
    # Len vlastná náhrada v ``context()`` — ``monkeypatch.undo()`` by vrátil aj presmerovanie dátového
    # priečinka z prípravku a ``sweep_trash`` by siahol na kôš živého kokpitu.
    with monkeypatch.context() as m:
        m.setattr(sandbox.shutil, "rmtree", lambda *a, **k: None)  # napr. kontajner ešte píše
        sandbox.discard(trashed)
    assert trashed.exists()
    assert sandbox.trash_dir() == trashed.parent  # stále dočasný priečinok, nie živý
    assert sandbox.sweep_trash() == 1
    assert not trashed.exists()


def test_sweep_without_a_trash_does_nothing(project):
    assert sandbox.sweep_trash() == 0
