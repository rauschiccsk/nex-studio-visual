"""The build sandbox for Príprava/Návrh — the MOUNT LIST is the guarantee, so the mount list is the test.

These are argv tests: no container is ever started (``asyncio.create_subprocess_exec`` is mocked and we
inspect the argv it was handed). That is not a compromise, it is the right level — what protects one
customer's production tree from another customer's build is precisely which ``--mount`` arguments docker is
given, and nothing that happens inside the container can add one.

WHY THE NEGATIVE HALF IS A WHITELIST AND NOT A LIST OF FORBIDDEN STRINGS. The first version of this file
asserted that seven literal substrings (``docker.sock``, ``/opt/customers``, …) were absent from the joined
argv. Audit showed what that does not catch: adding ``--mount source=/,target=/host`` (the WHOLE host disk),
``--mount source=/home/icc,target=/home/icc`` (the knowledge base) and ``--mount source=/opt,target=/opt-all``
(every customer) left all 40 tests green — the sandbox would have been handed MORE than the backend had, and
the test that exists to stop exactly that said nothing. Same for a second ``-u 0:0`` after ``--user
1000:1000`` (docker honours the LAST one, so the container ran as root while the "never root" test passed),
and for adding ``AWS_SECRET_ACCESS_KEY`` to the env allow-list.

A deny-list cannot be completed — that is this module's own lesson, and the test was breaking it. So the
assertions here are EQUALITIES over a parsed argv (:func:`_scan`):

  * every docker option before the image must be a member of :data:`_BOOLEAN_OPTS` / :data:`_VALUED_OPTS`;
    anything else — ``--network``, ``--pid``, ``--cap-add``, ``--privileged``, a second ``-u`` — is an
    unknown token and fails;
  * the ``--mount`` set, the ``-e`` set and the ``--user`` value are compared as SETS against what the
    turn is supposed to have, so an ADDITION fails as loudly as a removal.

:func:`test_the_structural_check_catches_every_addition_the_audit_slipped_through` runs the audit's own
mutations against the checker and proves each one now fails.

Scope reminder that these tests also pin: since STEP 2, ``priprava``, ``navrh`` and ``programovanie`` route
here (``test_every_other_phase_still_runs_as_a_subprocess``). Vizuál and Verifikácia still run as backend
subprocesses with the host mounts, because they bring the whole app up through ``docker compose``.

STEP 2 ADDS A SECOND THING TO CHECK, AND IT IS CHECKED THE SAME WAY. Programovanie is in here only because
the engine hands it a throwaway PostgreSQL instead of the docker socket it used to need. So the tests assert
that the database is really reachable-by-construction (same network, matching alias in the URL), that
Programovanie's MOUNT SET is byte-for-byte the one Príprava gets — a database is not an excuse for one extra
path — and that the container and the network are destroyed on every exit path. The docker control calls are
recorded rather than executed (:func:`docker_calls`), so what is asserted is the exact argv, never a mock's
opinion of it.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.services import build_db, build_sandbox, claude_agent, orchestrator


@pytest.fixture(autouse=True)
def _pin_projects_root(monkeypatch):
    """These tests assert the FIXED ``/opt/projects`` container→host translation, so ``PROJECTS_ROOT`` must
    be the real one here. The suite-wide isolation fixture points it at a temp dir so scaffolding writes
    cannot pollute the real workspace; these tests scaffold nothing (docker is mocked), so pinning the real
    prefix is both correct and leak-free — the same posture ``test_consult_sandbox`` takes."""
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", Path("/opt/projects"))


@pytest.fixture(autouse=True)
def _sandbox_on(monkeypatch, tmp_path):
    """Default ON, pinned image, and a THROWAWAY Claude home.

    ``prepare_turn`` creates and chowns ``<claude home>/projects/<mangled>``; pointing the module constant
    at ``tmp_path`` keeps every test off the real ``/home/andros/.claude``.
    """
    monkeypatch.delenv("BUILD_SANDBOX", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    monkeypatch.setattr(build_sandbox.settings, "build_sandbox_image", "nex-studio-visual-backend:vTEST")
    monkeypatch.setattr(build_sandbox, "_CLAUDE_HOME_DIR", str(tmp_path / "claude-home"))


@pytest.fixture(autouse=True)
def docker_calls(monkeypatch) -> list[tuple[str, ...]]:
    """Record every docker CONTROL call :mod:`build_db` makes; execute none of them.

    The seam is deliberately :func:`build_db._docker` — the ONE place the module shells out — rather than
    the public ``start``/``release``. Stubbing those would leave the argv that creates the network, starts
    the database and reaps both entirely unasserted, which is the same mistake the old deny-list version of
    this file made: the thing that carries the guarantee has to be the thing the test reads.
    """
    calls: list[tuple[str, ...]] = []

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        calls.append(tuple(args))
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    return calls


def _control(calls: list[tuple[str, ...]], *prefix: str) -> tuple[str, ...]:
    """The one recorded docker call starting with ``prefix`` (fails loudly on none / several)."""
    matches = [c for c in calls if c[: len(prefix)] == prefix]
    assert len(matches) == 1, f"expected exactly one {' '.join(prefix)} call, got {matches}"
    return matches[0]


def _ok_proc(envelope: dict | None = None) -> MagicMock:
    """A subprocess mock whose ``communicate()`` returns a valid ``--output-format json`` envelope, exit 0."""
    proc = MagicMock()
    proc.returncode = 0
    body = envelope if envelope is not None else {"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}
    proc.communicate = AsyncMock(return_value=(json.dumps(body).encode("utf-8"), b""))
    return proc


def _streaming_proc(events: list[dict]) -> MagicMock:
    """A subprocess mock that behaves like ``--output-format stream-json``: NDJSON lines on stdout.

    THE PRODUCTION SHAPE. ``orchestrator.invoke_agent`` always passes ``on_event``, so every live
    Príprava/Návrh turn takes :func:`claude_agent._invoke_streaming`; the whole first version of this file
    drove the non-streaming branch (``grep -c on_event`` was 0), which is why deleting the container reap
    from the streaming timeout handler kept 103 tests green.
    """
    proc = MagicMock()
    proc.returncode = 0

    async def _lines():
        for evt in events:
            yield (json.dumps(evt) + "\n").encode("utf-8")

    proc.stdout = _lines()
    proc.stderr = None
    proc.wait = AsyncMock(return_value=0)
    return proc


def _failing_proc(returncode: int, stderr: bytes) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


async def _turn_argv(monkeypatch, *, stage: str, slug: str = "acme", **kwargs) -> list[str]:
    """Drive one build turn with the subprocess mocked; return the argv it was launched with."""
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    await claude_agent.invoke_claude(
        project_slug=slug,
        claude_session_id=uuid4(),
        prompt="zadanie",
        stage=stage,
        **kwargs,
    )
    return list(mock_exec.call_args.args)


async def _streaming_turn_argv(monkeypatch, *, stage: str, slug: str = "acme", **kwargs) -> list[str]:
    """Same, through the STREAMING branch the live orchestrator actually uses."""
    mock_exec = AsyncMock(return_value=_streaming_proc([{"type": "result", "result": "ok"}]))
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)

    async def _on_event(_evt: dict) -> None:
        return None

    await claude_agent.invoke_claude(
        project_slug=slug,
        claude_session_id=uuid4(),
        prompt="zadanie",
        stage=stage,
        on_event=_on_event,
        **kwargs,
    )
    return list(mock_exec.call_args.args)


# ---------------------------------------------------------------------------
# The structural parser — the whole negative half rests on it
# ---------------------------------------------------------------------------

#: Docker options the sandbox is allowed to pass with NO value.
_BOOLEAN_OPTS = frozenset({"--rm", "--cap-drop=ALL"})
#: Docker options the sandbox is allowed to pass WITH exactly one value.
_VALUED_OPTS = frozenset({"--name", "--user", "--security-opt", "--mount", "-e", "-w", "--entrypoint", "--network"})


def _scan(argv: list[str]) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Parse ``docker run <opts…> <image> <claude args…>``; reject ANY option outside the known set.

    Returns ``(options, entrypoint_args)``. Raises ``AssertionError`` on the first unknown token — which is
    the point: an addition nobody reviewed is an unknown token.
    """
    assert argv[:2] == ["docker", "run"], f"not a docker run argv: {argv[:3]}"
    image = build_sandbox.sandbox_image()
    opts: list[tuple[str, str | None]] = []
    i = 2
    while i < len(argv):
        tok = argv[i]
        if tok == image:
            return opts, argv[i + 1 :]
        if tok in _BOOLEAN_OPTS:
            opts.append((tok, None))
            i += 1
        elif tok in _VALUED_OPTS:
            assert i + 1 < len(argv), f"{tok} has no value"
            opts.append((tok, argv[i + 1]))
            i += 2
        else:
            raise AssertionError(f"unknown docker option {tok!r} in the sandbox argv — nothing may be added unreviewed")
    raise AssertionError(f"image {image!r} never appears in the argv")


def _values(opts: list[tuple[str, str | None]], flag: str) -> list[str]:
    return [v for f, v in opts if f == flag and v is not None]


def _mount_specs(opts: list[tuple[str, str | None]]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for spec in _values(opts, "--mount"):
        fields: dict[str, str] = {}
        for part in spec.split(","):
            key, _, value = part.partition("=")
            fields[key] = value
        parsed.append(fields)
    return parsed


def _value_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _mount_identity(opts: list[tuple[str, str | None]]) -> set[tuple[str, str, str, bool]]:
    """The mount list reduced to ``(type, source, target, readonly)`` — the form the EQUALITIES compare.

    One helper, used by every phase's mount assertion, so "Príprava's mounts" and "Programovanie's mounts"
    cannot be checked by two subtly different rules that drift apart.
    """
    return {
        (
            m.get("type", ""),
            m.get("source", m.get("destination", "")),
            m.get("target", m.get("destination", "")),
            "readonly" in m,
        )
        for m in _mount_specs(opts)
    }


def _expected_mounts(slug: str) -> set[tuple[str, str, str, bool]]:
    """The five mounts a project with no ``.claude`` config to freeze is entitled to — and no others."""
    return {
        ("tmpfs", "/home/andros", "/home/andros", False),
        (
            "bind",
            str(Path(build_sandbox._CLAUDE_HOME_DIR) / "projects" / f"-opt-projects-{slug}"),
            f"/home/andros/.claude/projects/-opt-projects-{slug}",
            False,
        ),
        ("bind", f"/opt/projects/{slug}", f"/opt/projects/{slug}", False),
        ("bind", build_sandbox._KB_DIR, build_sandbox._KB_DIR, True),
        ("bind", "/home/andros/.local", "/home/andros/.local", True),
    }


# ---------------------------------------------------------------------------
# (a) the project is mounted WRITABLE, at the path the backend already drives claude with
# ---------------------------------------------------------------------------


async def test_project_is_mounted_writable_at_the_backend_container_path(monkeypatch) -> None:
    argv = await _turn_argv(monkeypatch, stage="priprava", slug="acme")
    opts, _ = _scan(argv)
    project = next(m for m in _mount_specs(opts) if m.get("target") == "/opt/projects/acme")
    assert project["type"] == "bind"
    assert project["source"] == "/opt/projects/acme"
    # RW is the ONE deliberate difference from the read-only Konzultácia sidecar: a build has to write.
    assert "readonly" not in project
    # Same in-container path as the in-process turn → --resume, cwd and relative paths resolve identically.
    assert _values(opts, "-w") == ["/opt/projects/acme"]


async def test_bind_uses_mount_not_v_so_a_missing_source_is_refused(monkeypatch) -> None:
    # ``-v`` CREATES an absent host source as an empty dir — the build would then run against an EMPTY
    # project and produce confident nonsense with no error anywhere. ``--mount`` refuses instead.
    argv = await _turn_argv(monkeypatch, stage="navrh")
    assert "-v" not in argv


async def test_claude_flags_are_unchanged_by_the_wrapper(monkeypatch) -> None:
    # Only the TRANSPORT changes: the per-turn claude argv is the same one the in-process turn builds,
    # appended after the image, with ``--entrypoint claude`` supplying the dropped binary name.
    argv = await _turn_argv(monkeypatch, stage="priprava")
    opts, entrypoint_args = _scan(argv)
    assert _values(opts, "--entrypoint") == ["claude"]
    assert entrypoint_args[0] == "-p"
    assert _value_after(entrypoint_args, "--output-format") == "json"
    assert entrypoint_args[-1].endswith("zadanie") or "zadanie" in entrypoint_args[-1]
    assert "--allowedTools" not in entrypoint_args  # a build turn keeps its full profile


# ---------------------------------------------------------------------------
# (b) the NEGATIVE half — set equality, not a deny-list
# ---------------------------------------------------------------------------


async def test_the_mount_list_is_exactly_these_five_and_nothing_else(monkeypatch) -> None:
    """The whole guarantee, stated as an EQUALITY so an ADDITION fails.

    Five mounts, for a project (``acme``) that has no ``.claude`` config to freeze: the ephemeral HOME, this
    project's own transcript directory, the project itself, the read-only ``claude`` binary, and the shared
    knowledge base READ-ONLY (charter §3(1); Director 23.08.2026). Every other path the BACKEND holds — the
    docker socket, ``/opt/customers``, ``/opt/uat``, ``/opt/infra``, the credential store, and the shared
    ``~/.claude`` that used to be here — is simply not in this set, and cannot be added without turning this
    red. The knowledge base being in the set with ``readonly=True`` is itself the assertion: mounted, and
    writes kernel-refused.
    """
    argv = await _turn_argv(monkeypatch, stage="priprava", slug="acme")
    opts, _ = _scan(argv)
    assert _mount_identity(opts) == _expected_mounts("acme")


async def test_the_shared_claude_config_dir_is_gone_and_home_is_ephemeral(monkeypatch) -> None:
    """Audit's proven escape: ``/home/andros/.claude`` was mounted WRITABLE and is the SAME directory the
    root backend container uses, holding ``settings.json`` (``bypassPermissions`` + UserPromptSubmit /
    PostToolUse / Stop hooks) and ``hooks/wip-recorder.sh``, all owned by uid 1000 — the sandbox's own uid.
    A poisoned hook is executed by the next Programovanie turn (root, docker socket, /opt/customers rw) and
    by every interactive session on the host. The same mount also exposed 22 other projects' transcripts and
    ``history.jsonl``. It is not mounted any more; HOME is a tmpfs and only THIS project's transcript
    directory is bound back into it."""
    argv = await _turn_argv(monkeypatch, stage="priprava", slug="acme")
    opts, _ = _scan(argv)
    mounts = _mount_specs(opts)

    home = next(m for m in mounts if m.get("destination") == "/home/andros")
    assert home["type"] == "tmpfs"

    claude_mounts = [m for m in mounts if m.get("target", "").startswith("/home/andros/.claude")]
    assert [m["target"] for m in claude_mounts] == ["/home/andros/.claude/projects/-opt-projects-acme"]
    # …and its SOURCE is one project's directory, never the config dir or the projects root.
    source = claude_mounts[0]["source"]
    assert source.endswith("/projects/-opt-projects-acme")
    assert not any(m.get("source") == build_sandbox._CLAUDE_HOME_DIR for m in mounts)
    # history.jsonl / other projects' transcripts / the user settings are all under paths not mounted.
    joined = " ".join(argv)
    for gone in ("history.jsonl", "/home/andros/.claude/settings.json", "/home/andros/.claude/hooks"):
        assert gone not in joined


@pytest.mark.parametrize("stage", build_sandbox.SANDBOXED_PHASES)
async def test_a_second_user_flag_or_any_unknown_option_is_impossible(monkeypatch, stage) -> None:
    """EVERY sandboxed phase, not just the first one somebody wrote a test for.

    This ran with ``stage="priprava"`` alone, and audit showed what that costs: appending
    ``argv += ["--user", "0:0"]`` inside ``build_run_argv``'s ``if database is not None:`` branch — the
    Programovanie-only block — left the whole suite GREEN at 112 passed, while docker (which honours the
    LAST ``--user``) would have run the riskiest phase as ROOT. The same mutation applied unconditionally is
    red here, which is the proof that the assertion was fine and its COVERAGE was the hole. Parametrising on
    :data:`build_sandbox.SANDBOXED_PHASES` also means a phase added to that tuple is checked the day it is
    added, without anyone remembering to widen this.
    """
    argv = await _turn_argv(monkeypatch, stage=stage)
    opts, _ = _scan(argv)
    # Exactly ONE --user. Audit added ``-u 0:0`` after it and the container ran as ROOT while the old
    # "never root" test — which read the FIRST --user — stayed green.
    assert _values(opts, "--user") == ["1000:1000"]
    assert "-u" not in argv
    assert "--privileged" not in argv
    # Capabilities dropped and no way back up through a setuid binary.
    assert ("--cap-drop=ALL", None) in opts
    assert _values(opts, "--security-opt") == ["no-new-privileges"]


@pytest.mark.parametrize("stage", build_sandbox.SANDBOXED_PHASES)
async def test_the_env_allow_list_is_exactly_this_set(monkeypatch, stage) -> None:
    """An EQUALITY, because audit added ``AWS_SECRET_ACCESS_KEY`` and ``NEX_MASTER_KEY`` to the pass-through
    list and every test stayed green.

    …and an equality that runs for EVERY sandboxed phase, because audit then put ``-e NEX_MASTER_KEY`` into
    the ``if database is not None:`` branch and the suite stayed green at 112 passed: this test only ever
    drove ``navrh``, and Programovanie — the phase whose own docstring calls it the one that can really hurt
    somebody — was covered by nothing stronger than "there is exactly one ``DATABASE_URL=``". The database
    is the ONLY difference a phase is allowed to make.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-never-appear")
    argv = await _turn_argv(monkeypatch, stage=stage)
    opts, _ = _scan(argv)
    env = _values(opts, "-e")
    by_name = {e for e in env if "=" not in e}
    by_value = {e.split("=", 1)[0] for e in env if "=" in e}
    # The one legitimate per-phase difference: Programovanie is handed its throwaway database BY VALUE.
    extra_by_value = {"DATABASE_URL"} if build_db.phase_needs_database(stage) else set()
    assert by_name == {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "IS_SANDBOX",
        "DISABLE_AUTOUPDATER",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "LANG",
        # Charter §3(1) reaches the KB two ways; the file mount alone revives only one, because the
        # project's own rag_query.py talks to Qdrant directly (Director 23.08.2026).
        "QDRANT_URL",
        "OLLAMA_URL",
    }
    assert (
        by_value
        == {
            "HOME",
            "CLAUDE_CONFIG_DIR",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0",
            "GIT_CONFIG_VALUE_0",
        }
        | extra_by_value
    )
    # Env handed over BY NAME → docker copies the value from its own environment, so no secret lands in an
    # argv that anybody on the host can read out of ``ps``.
    assert "ghp-should-never-appear" not in " ".join(argv)
    for never in ("TEST_DATABASE_URL", "SECRET_KEY", "DEDO_API_TOKEN", "DEDO_CHANNEL_DIR"):
        assert never not in " ".join(argv)
    # ``DATABASE_URL`` is never passed BY NAME (that would copy the BACKEND's own production URL); the
    # database phase carries exactly one, composed by value — pinned in full by
    # ``test_the_database_url_can_never_be_the_studios_own``.
    assert "DATABASE_URL" not in by_name
    assert ("DATABASE_URL" in by_value) is build_db.phase_needs_database(stage)


def test_the_structural_check_catches_every_addition_the_audit_slipped_through() -> None:
    """Proof that the checker above is worth anything: each mutation audit made must now FAIL.

    Every one of these left the old deny-list suite at "40 passed"; three of them hand the sandbox more than
    the backend itself holds, and the ``-u 0:0`` one makes the container run as root (docker honours the
    LAST occurrence — verified live: ``docker run --user 1000:1000 -u 0:0 … id`` → ``uid=0(root)``).
    """
    base = build_sandbox.build_run_argv(
        project_slug="acme", container_name="nex-build-test", claude_argv=["claude", "-p", "x"]
    )
    _scan(base)  # the honest argv parses
    mutations = (
        ["--mount", "type=bind,source=/,target=/host"],
        ["--mount", "type=bind,source=/home/icc,target=/home/icc"],
        ["--mount", "type=bind,source=/opt,target=/opt-all"],
        ["--mount", "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock"],
        ["-u", "0:0"],
        ["--cap-add=SYS_ADMIN"],
        ["--pid=host"],
        ["--network=host"],
        ["--security-opt", "apparmor=unconfined"],
        ["--privileged"],
        ["-e", "AWS_SECRET_ACCESS_KEY"],
        # STEP 2 made ``--network`` a LEGAL option, which is exactly how a checker starts letting things
        # through: ``--network host`` now parses, so the scanner alone no longer stops it and the equality
        # below has to. A base argv with no database must carry no ``--network`` at all.
        ["--network", "host"],
    )
    for extra in mutations:
        mutated = base[:2] + extra + base[2:]
        try:
            opts, _ = _scan(mutated)
        except AssertionError:
            continue  # an unknown option — caught by the scanner itself
        # A KNOWN option added a second time is caught by the set/count equalities instead.
        mounts = {(m.get("source"), m.get("target")) for m in _mount_specs(opts)}
        env = set(_values(opts, "-e"))
        caught = (
            len(_values(opts, "--user")) != 1
            or len(_values(opts, "--security-opt")) != 1
            or any(src in ("/", "/opt", "/home/icc", "/var/run/docker.sock") for src, _ in mounts)
            or "AWS_SECRET_ACCESS_KEY" in env
            # No database was planned for this argv, so no network may be joined either.
            or _values(opts, "--network") != []
        )
        assert caught, f"mutation {extra} slipped through the structural check"


# ---------------------------------------------------------------------------
# (b2) the project's own executable config is re-deployed READ-ONLY on every dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def scaffolded_project(monkeypatch, tmp_path):
    """A project shaped like one Create Project makes: hooks in ``.claude/settings.json`` pointing at
    scripts INSIDE the project, plus the per-role deny profiles under ``.claude/agents/``."""
    root = tmp_path / "projects"
    proj = root / "acme"
    (proj / ".claude" / "agents" / "ai-agent").mkdir(parents=True)
    (proj / ".claude" / "skills").mkdir()
    (proj / "scripts").mkdir()
    (proj / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {"defaultMode": "bypassPermissions"},
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write|MultiEdit",
                            "hooks": [
                                {"type": "command", "command": f"{proj}/scripts/hook_rag_reindex.sh", "timeout": 60},
                                {"type": "command", "command": f"{proj}/scripts/hook_agent_notify.sh --quiet"},
                                {"type": "command", "command": "/opt/projects/nex-studio/scripts/outside.sh"},
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (proj / ".claude" / "agents" / "ai-agent" / "settings.json").write_text("{}", encoding="utf-8")
    for name in ("hook_rag_reindex.sh", "hook_agent_notify.sh"):
        (proj / "scripts" / name).write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(build_sandbox, "_CONTAINER_TO_HOST_PREFIX", ((str(root), str(root)),))
    return proj


def test_every_file_that_decides_what_a_later_root_turn_executes_is_read_only(scaffolded_project) -> None:
    """The second proven escape: the hooks and deny profiles live in the deliberately WRITABLE project mount.

    ``.claude/settings.json`` carries ``PostToolUse`` shell commands; ``.claude/agents/<role>/settings.json``
    is the deny profile that is the only thing surviving ``bypassPermissions``. A sandboxed Príprava/Návrh
    turn could rewrite either, and the next Programovanie/Verifikácia turn — root, docker socket,
    ``/opt/customers`` — would run the result or run without the restraint. They are re-mounted read-only on
    top of the read-write project bind, together with the hook SCRIPTS the settings name (freezing the
    settings while leaving the script writable would be theatre).
    """
    argv = build_sandbox.build_run_argv(
        project_slug="acme", container_name="nex-build-test", claude_argv=["claude", "-p", "x"]
    )
    opts, _ = _scan(argv)
    ro = {m["target"] for m in _mount_specs(opts) if "readonly" in m}
    proj = str(scaffolded_project)
    assert f"{proj}/.claude/settings.json" in ro
    assert f"{proj}/.claude/agents" in ro
    assert f"{proj}/.claude/skills" in ro
    assert f"{proj}/scripts/hook_rag_reindex.sh" in ro
    # …arguments after the command are stripped before the path is resolved.
    assert f"{proj}/scripts/hook_agent_notify.sh" in ro
    # A hook pointing OUTSIDE the project needs no freezing — the sandbox cannot reach it at all.
    assert not any("/opt/projects/nex-studio/scripts/outside.sh" in t for t in ro)
    # The project itself stays writable — a build has to write.
    project_mount = next(m for m in _mount_specs(opts) if m.get("target") == proj)
    assert "readonly" not in project_mount


def test_absent_config_is_not_invented_and_is_neutralised_at_load_time(scaffolded_project) -> None:
    """``--mount`` refuses an absent source and writing a file into a customer repository to be able to
    freeze it would be worse than the problem. The two files a turn could CREATE are therefore killed where
    they would be READ: ``settings.local.json`` by ``--setting-sources user,project`` and ``.mcp.json`` (MCP
    servers are spawned processes) by ``--strict-mcp-config`` — on every turn, sandboxed or not."""
    assert not (scaffolded_project / ".claude" / "settings.local.json").exists()
    assert not (scaffolded_project / ".mcp.json").exists()
    frozen = build_sandbox.frozen_project_paths(str(scaffolded_project))
    assert ".claude/settings.local.json" not in frozen
    assert ".mcp.json" not in frozen

    argv = claude_agent.build_claude_argv(streaming=False, claude_session_id=uuid4(), prompt="p", charter_text=None)
    assert _value_after(argv, "--setting-sources") == "user,project"
    assert "--strict-mcp-config" in argv


def test_a_hook_command_escaping_the_project_is_never_mounted(scaffolded_project) -> None:
    """A traversal in a hook command must not become a bind source — the freeze list is derived from a file
    the project owns, so it is untrusted input like any other."""
    settings = scaffolded_project / ".claude" / "settings.json"
    settings.write_text(
        json.dumps({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "../../../etc/shadow"}]}]}}),
        encoding="utf-8",
    )
    frozen = build_sandbox.frozen_project_paths(str(scaffolded_project))
    assert all(not f.startswith("..") for f in frozen)
    assert "/etc/shadow" not in " ".join(frozen)


def test_broken_project_settings_do_not_break_the_sandbox(scaffolded_project) -> None:
    (scaffolded_project / ".claude" / "settings.json").write_text("{ not json", encoding="utf-8")
    frozen = build_sandbox.frozen_project_paths(str(scaffolded_project))
    # The settings file itself is still frozen (it exists); only its unparseable hook list is skipped.
    assert ".claude/settings.json" in frozen


# ---------------------------------------------------------------------------
# (b3) the permission posture is in the ARGV, not in a file the turn can rewrite
# ---------------------------------------------------------------------------


async def test_sandboxed_turn_carries_bypass_permissions_as_a_flag(monkeypatch) -> None:
    """Auto-approval used to come from ``defaultMode: bypassPermissions`` inside the mounted user
    ``settings.json`` — i.e. the turn's own permission posture was a file the turn could edit. With the
    tmpfs HOME that file is gone; the mode is a flag, and a flag cannot be edited from inside the container.
    """
    argv = await _turn_argv(monkeypatch, stage="priprava")
    _, entrypoint_args = _scan(argv)
    assert _value_after(entrypoint_args, "--permission-mode") == "bypassPermissions"


async def test_an_in_process_build_turn_is_byte_identical_to_before(monkeypatch) -> None:
    # ``verifikacia``, not ``programovanie``: STEP 2 moved Programovanie INTO the sandbox, and this test
    # exists to pin what an UNSANDBOXED turn still looks like.
    argv = await _turn_argv(monkeypatch, stage="verifikacia")
    assert argv[0] == "claude"
    assert "--permission-mode" not in argv


async def test_git_identity_travels_with_the_sandbox(monkeypatch) -> None:
    """``HOME`` moved from ``/root`` (where the image's ``.gitconfig`` lives) to ``/home/andros`` (where it
    does not), so ``git config --global --list`` failed and every ``git commit`` the agent made itself died
    on "Author identity unknown" — while ``GITHUB_TOKEN``/``GH_TOKEN`` were passed in for "the agent commits
    and pushes its own work". Verified live: with these env vars a commit inside the sandbox succeeds."""
    argv = await _turn_argv(monkeypatch, stage="navrh")
    opts, _ = _scan(argv)
    env = dict(e.split("=", 1) for e in _values(opts, "-e") if "=" in e)
    assert env["GIT_AUTHOR_NAME"] == "NEX Studio"
    assert env["GIT_AUTHOR_EMAIL"] == "studio@isnex.eu"
    assert env["GIT_COMMITTER_NAME"] == "NEX Studio"
    assert env["GIT_COMMITTER_EMAIL"] == "studio@isnex.eu"


# ---------------------------------------------------------------------------
# (c) ownership — the sandbox is uid 1000, every previous turn ran as root
# ---------------------------------------------------------------------------


def test_session_dir_name_matches_what_the_cli_actually_uses() -> None:
    # Confirmed on the live host (``-opt-projects-nex-productcatalogs``) and end-to-end in a real sandboxed
    # turn: turn 1 wrote its transcript into exactly this directory and turn 2 ``--resume``d it.
    assert build_sandbox.session_dir_name("/opt/projects/acme") == "-opt-projects-acme"
    assert build_sandbox.session_dir_name("/opt/projects/nex-productcatalogs") == "-opt-projects-nex-productcatalogs"


async def test_prepare_creates_the_transcript_dir_a_new_project_has_not_got(monkeypatch) -> None:
    """``--mount`` (correctly) refuses an absent source, so without this a brand-new project would report
    the sandbox unavailable on its very first turn."""
    session = Path(build_sandbox._CLAUDE_HOME_DIR) / "projects" / "-opt-projects-acme"
    assert not session.exists()
    await build_sandbox.prepare_turn("acme")
    assert session.is_dir()


async def test_prepare_reowns_root_owned_leftovers(monkeypatch, tmp_path) -> None:
    """Root-owned transcripts made ``--resume`` die with "No conversation found" (reproduced live), and
    root-owned project files made ``Write``/``Edit`` fail with EACCES on the very Špecifikácia the phase
    exists to update. Both are re-owned before the container starts — and per TURN, not once at deploy,
    because Programovanie/Vizuál/Verifikácia still run as root and re-create the state."""
    root = tmp_path / "projects"
    (root / "acme" / "docs").mkdir(parents=True)
    (root / "acme" / "docs" / "specification.md").write_text("spec", encoding="utf-8")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(build_sandbox, "_CONTAINER_TO_HOST_PREFIX", ((str(root), str(root)),))

    chowned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(build_sandbox.os, "lchown", lambda p, u, g: chowned.append((p, u, g)))
    # Pretend everything on disk belongs to root, as it does after a non-sandboxed phase. ``st_nlink=1``
    # says "one directory entry for this inode" — an ordinary file; the multiply-linked case has its own
    # test below, because it is the one the chown must NOT touch.
    real_lstat = build_sandbox.os.lstat
    monkeypatch.setattr(
        build_sandbox.os,
        "lstat",
        lambda p: type("S", (), {"st_uid": 0, "st_gid": 0, "st_mode": real_lstat(p).st_mode, "st_nlink": 1})(),
    )

    await build_sandbox.prepare_turn("acme")
    targets = {p for p, _, _ in chowned}
    assert str(root / "acme" / "docs" / "specification.md") in targets
    session = Path(build_sandbox._CLAUDE_HOME_DIR) / "projects" / build_sandbox.session_dir_name(str(root / "acme"))
    assert str(session) in targets
    assert all((u, g) == (1000, 1000) for _, u, g in chowned)


async def test_a_hard_link_in_the_project_is_never_chowned_to_the_sandbox_uid(monkeypatch, tmp_path, caplog) -> None:
    """The mount list bounds PATHS; a hard link is how something outside gets a path inside.

    Audit planted a second directory entry, inside a project tree, for an inode living OUTSIDE every mount
    root — legal because ``/opt/projects``, ``/opt/customers``, ``/opt/uat`` and ``/opt/infra`` are one
    device on this host — and the sandbox read AND wrote the file through it. The preparation step made it
    worse rather than catching it: ``lchown`` cannot tell a hard link from an ordinary file, so this step
    would hand the sandbox uid a foreign, root-only inode and thereby GRANT the write it exists to scope.

    So: a regular file with ``st_nlink > 1`` is skipped and reported at ERROR. Directories always have
    ``st_nlink > 1`` and must be unaffected — that is asserted here too, because skipping them would leave
    every project directory root-owned and break the sandbox outright.
    """
    root = tmp_path / "projects"
    (root / "acme").mkdir(parents=True)
    ordinary = root / "acme" / "app.py"
    ordinary.write_text("code", encoding="utf-8")
    # The bait lives outside the projects root, on the same filesystem — exactly the audit's shape.
    bait = tmp_path / "outside-the-roots.txt"
    bait.write_text("SECRET-OUTSIDE-THE-SANDBOX-ROOTS", encoding="utf-8")
    link = root / "acme" / "innocent-looking.txt"
    os.link(bait, link)
    assert os.stat(link).st_ino == os.stat(bait).st_ino

    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(build_sandbox, "_CONTAINER_TO_HOST_PREFIX", ((str(root), str(root)),))
    chowned: list[str] = []
    monkeypatch.setattr(build_sandbox.os, "lchown", lambda p, u, g: chowned.append(p))
    # Everything looks root-owned (the state a non-sandboxed phase leaves), so nothing is skipped merely for
    # already having the right owner — the REAL ``st_mode`` and ``st_nlink`` are kept, which is the point.
    real_lstat = build_sandbox.os.lstat
    monkeypatch.setattr(
        build_sandbox.os,
        "lstat",
        lambda p: type(
            "S", (), {"st_uid": 0, "st_gid": 0, "st_mode": real_lstat(p).st_mode, "st_nlink": real_lstat(p).st_nlink}
        )(),
    )

    # ``backend.main`` sets ``propagate = False`` on the ``backend`` logger (its own stderr handler), so
    # caplog — which listens on the root logger — sees nothing unless propagation is restored for the test.
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)
    with caplog.at_level(logging.ERROR, logger=build_sandbox.logger.name):
        await build_sandbox.prepare_turn("acme")

    assert str(link) not in chowned, "a multiply-linked file must never be re-owned to the sandbox uid"
    assert str(ordinary) in chowned, "an ordinary file still has to be re-owned or the sandbox cannot write"
    assert str(root / "acme") in chowned, "directories always have st_nlink > 1 and must NOT be skipped"
    # …and it is never silent: nothing else in the pipeline would ever mention this file.
    reported = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert any("hard link" in msg and str(link) in msg for msg in reported), reported


async def test_prepare_failure_is_unavailability_not_a_silent_downgrade(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(build_sandbox.os, "makedirs", _boom)
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        await claude_agent.invoke_claude(project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava")
    mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# (d) a traversal slug must never widen the mount
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    ["..", "../other", "../../etc", "/", "", ".", "a/b", "a/../../etc", "foo/..", "/opt", "-leading"],
)
def test_traversal_slug_raises_before_any_mount_is_composed(bad_slug) -> None:
    # An unvalidated ``..`` composes the source ``/opt/projects/..`` → docker mounts ALL of /opt (every
    # customer, every UAT deployment) INTO THE SANDBOX, WRITABLE. The slug must die at this boundary.
    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        build_sandbox.build_run_argv(
            project_slug=bad_slug, container_name="nex-build-test", claude_argv=["claude", "-p", "x"]
        )


async def test_traversal_slug_never_launches_anything(monkeypatch) -> None:
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        await claude_agent.invoke_claude(
            project_slug="../other", claude_session_id=uuid4(), prompt="p", stage="priprava"
        )
    mock_exec.assert_not_called()
    # …and the per-turn PREPARATION, which runs first and creates/chowns directories, refused it too —
    # otherwise a traversal slug would have composed a transcript directory before the mount composer ever
    # saw it.
    assert not (Path(build_sandbox._CLAUDE_HOME_DIR) / "projects").exists()


def test_containment_assert_rejects_a_source_that_escapes_the_root() -> None:
    # Belt-and-suspenders, independent of the slug rule: even a symlink or a future prefix change that
    # composed an escaping source is refused rather than silently broadening a WRITABLE mount.
    from backend.services import sandbox_paths

    prefixes = build_sandbox._CONTAINER_TO_HOST_PREFIX
    for escaping in ("/opt/projects/../secret", "/opt", "/opt/customers/mager"):
        with pytest.raises(sandbox_paths.SandboxPathError):
            sandbox_paths.assert_host_source_contained(escaping, prefixes, sandbox="build sandbox")
    sandbox_paths.assert_host_source_contained("/opt/projects/acme", prefixes, sandbox="build sandbox")


def test_good_slug_source_resolves_strictly_under_the_projects_root() -> None:
    argv = build_sandbox.build_run_argv(
        project_slug="acme", container_name="nex-build-test", claude_argv=["claude", "-p", "x"]
    )
    opts, _ = _scan(argv)
    source = next(m for m in _mount_specs(opts) if m.get("target") == "/opt/projects/acme")["source"]
    assert os.path.realpath(source).startswith(os.path.realpath("/opt/projects") + os.sep)


# ---------------------------------------------------------------------------
# (e) routing — the PHASE decides, three phases move, and NO call site may omit it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["priprava", "navrh", "vizual", "programovanie"])
async def test_the_sandboxed_phases_run_in_the_sandbox(monkeypatch, stage) -> None:
    assert (await _turn_argv(monkeypatch, stage=stage))[0] == "docker"


@pytest.mark.parametrize("stage", ["verifikacia", "hotovo", None, "", "unknown"])
async def test_every_other_phase_still_runs_as_a_subprocess(monkeypatch, stage) -> None:
    """Four phases of five are in. **Verifikácia** stays OUT and that is a decision, not an oversight: the
    independent Auditor is READ + RUN-ONLY and is told to check the app "oproti bežiacej appke, nie oproti
    slovu AI Agenta" — being able to boot the thing it judges IS the phase (§2.5). It is also the phase
    carrying the least accident risk of the five, because the Auditor never writes and never commits.

    **Vizuál** used to be listed here for a reason that turned out to be false (ICCINT-20): the phase does
    run the whole frontend, but the ENGINE runs it — ``vizual_sandbox.spin_up`` owns that container and
    bind-mounts the same host directory the sandbox writes to, so the agent's turn is file editing and HMR
    carries it. An unknown/absent phase also keeps today's behaviour."""
    assert (await _turn_argv(monkeypatch, stage=stage))[0] == "claude"


async def test_a_consult_turn_never_takes_the_build_sandbox(monkeypatch) -> None:
    # A read-only consult has its own sidecar; the build wrapper keys on a build profile
    # (``allowed_tools is None``) so the two can never collide even if a consult carried a build phase.
    argv = await _turn_argv(monkeypatch, stage="priprava", allowed_tools=["Read", "Grep", "Glob"])
    assert argv[0] == "claude"


def test_phase_gate_is_the_single_source_of_the_routing() -> None:
    assert build_sandbox.SANDBOXED_PHASES == ("priprava", "navrh", "vizual", "programovanie")
    assert build_sandbox.phase_uses_sandbox("priprava") is True
    assert build_sandbox.phase_uses_sandbox("NAVRH") is True  # normalised, not case-fragile
    assert build_sandbox.phase_uses_sandbox("vizual") is True
    assert build_sandbox.phase_uses_sandbox("programovanie") is True
    for other in ("verifikacia", None, "", 7):
        assert build_sandbox.phase_uses_sandbox(other) is False


def test_the_charter_tells_the_agent_which_phases_have_docker() -> None:
    """A rule the agent cannot see is a trap, not a rule (this module's docstring, §3(3)).

    ``SANDBOXED_PHASES`` decides what the agent's turn CAN do; the charter is the only place the agent
    learns it. When the two drift, the agent finds out by having something fail mid-build and — worse —
    may escalate a working engine as broken. ICCINT-20 is exactly that risk: before it, the charter told
    the agent in as many words that "Postaviť a spustiť CELÚ appku patrí do Vizuálu a Verifikácie — tie
    fázy Docker majú", which the code change makes false for Vizuál on the same day.

    Checked mechanically against the TEMPLATE (the source every new project's charter is written from),
    because the obligation is the kind of thing that gets remembered once and forgotten twice."""
    charter = (Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md").read_text(encoding="utf-8")
    # Verifikácia is the one phase with Docker, and the charter must not promise it anywhere else.
    assert "patrí do Verifikácie" in charter
    assert "patrí do Vizuálu a Verifikácie" not in charter, "the charter still promises Vizuál a docker socket"
    # Every sandboxed phase is named where the isolation is explained.
    assert "Príprava, Návrh a Vizuál" in charter
    assert "Vo Vizuáli Docker tiež nepotrebuješ a nemáš" in charter


def test_no_orchestrator_dispatch_may_omit_its_phase() -> None:
    """The hole this whole audit round opened with: ``invoke_agent`` was called "the single chokepoint every
    dispatch passes through". There are THREE ``invoke_claude`` call sites, and ``_plan_pass_once`` — the
    turn behind every task-plan pass in BOTH Príprava and Návrh — omitted ``stage=``. ``stage=None`` makes
    ``phase_uses_sandbox`` answer False, so the pass ran unisolated as root with ``/opt/customers``,
    ``/opt/uat``, ``/opt/infra`` and the docker socket in reach — and silently, because the "sandbox is off"
    WARNING lives behind the same phase check and the log line said ``transport=in-process``.

    A default that means "keep the dangerous behaviour" must be checked by a machine, not by reading, so
    this walks the orchestrator's AST. A phase that deliberately stays in-process says so explicitly
    (``stage="verifikacia"``); "not passed" is never a legitimate answer again.
    """
    tree = ast.parse(Path(orchestrator.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "invoke_claude"
        and "stage" not in {kw.arg for kw in node.keywords}
    ]
    assert offenders == [], f"invoke_claude call sites without stage= at orchestrator.py lines {offenders}"


# ---------------------------------------------------------------------------
# (f) the kill-switch
# ---------------------------------------------------------------------------


async def test_kill_switch_returns_every_phase_to_the_subprocess(monkeypatch, caplog) -> None:
    # ``backend.main`` sets ``propagate = False`` on the ``backend`` logger (its own stderr handler), so
    # caplog — which listens on the root logger — sees nothing unless propagation is restored for the test.
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("BUILD_SANDBOX", off)
        assert build_sandbox.sandbox_enabled() is False
        with caplog.at_level("WARNING", logger=claude_agent.logger.name):
            caplog.clear()
            argv = await _turn_argv(monkeypatch, stage="priprava")
        assert argv[0] == "claude"
        # Off is a deliberate operator choice, but it is never SILENT: each turn says what is back in reach.
        assert "BUILD_SANDBOX is off" in caplog.text
        assert "/opt/customers" in caplog.text


def test_switch_defaults_to_on_and_is_read_at_turn_time(monkeypatch) -> None:
    monkeypatch.delenv("BUILD_SANDBOX", raising=False)
    assert build_sandbox.sandbox_enabled() is True
    monkeypatch.setenv("BUILD_SANDBOX", "1")
    assert build_sandbox.sandbox_enabled() is True


@pytest.mark.parametrize("empty", ["", "   ", "\t"])
async def test_an_empty_value_means_unset_which_means_on(monkeypatch, empty) -> None:
    """``BUILD_SANDBOX=`` used to DISABLE the sandbox, which is the opposite of what a blank value says.

    It is not hypothetical: ``BUILD_SANDBOX=${BUILD_SANDBOX}`` with an unexported variable, and
    ``environment: - BUILD_SANDBOX`` without a value, both put exactly the empty string into the process
    environment. A variable somebody FORGETS is safe (the default is "1"); a variable somebody ADDS to a
    config and leaves blank must not be the one that switches a security boundary off.
    """
    monkeypatch.setenv("BUILD_SANDBOX", empty)
    assert build_sandbox.sandbox_enabled() is True
    assert (await _turn_argv(monkeypatch, stage="priprava"))[0] == "docker"


# ---------------------------------------------------------------------------
# (g) an unavailable sandbox is NEVER a quiet retreat to the subprocess
# ---------------------------------------------------------------------------


async def test_unreachable_daemon_fails_loudly_and_does_not_fall_back(monkeypatch) -> None:
    """The decision this pins: a build turn that loses its sandbox FAILS.

    Unlike the consult sidecar — whose fallback is still read-only by tool profile and so costs only
    defence-in-depth — a build turn falling back gets an unrestricted Bash tool inside a container mounting
    every customer's production tree. That fallback IS the exposure the sandbox removes, so taking it
    automatically would undo the change while every surface reported success. It fails instead, and
    ``BUILD_SANDBOX=0`` is the one way back — a switch somebody throws on purpose.
    """
    mock_exec = AsyncMock(
        return_value=_failing_proc(125, b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    )
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    with pytest.raises(build_sandbox.BuildSandboxUnavailable) as exc:
        await claude_agent.invoke_claude(project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava")
    # Exactly ONE launch — the docker one. No second, in-process attempt.
    assert mock_exec.call_count == 1
    assert mock_exec.call_args.args[0] == "docker"
    # And the message tells the operator both correct moves rather than leaving them to be guessed.
    assert "BUILD_SANDBOX=0" in str(exc.value)
    assert "BUILD_SANDBOX_IMAGE" in str(exc.value)


async def test_missing_image_is_unavailable_not_a_claude_crash(monkeypatch) -> None:
    monkeypatch.setattr(
        claude_agent.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_failing_proc(125, b"Unable to find image 'nex-studio-visual-backend:vTEST' locally")),
    )
    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        await claude_agent.invoke_claude(project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava")


async def test_missing_docker_cli_is_unavailable_not_a_bare_oserror(monkeypatch) -> None:
    monkeypatch.setattr(
        claude_agent.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError("docker")),
    )
    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        await claude_agent.invoke_claude(project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava")


async def test_claude_failing_inside_a_healthy_sandbox_stays_a_claude_error(monkeypatch) -> None:
    # The distinction matters: this one is the agent's problem and is handled/retried like any other turn;
    # only an infrastructure failure means the GUARANTEE could not be established.
    monkeypatch.setattr(
        claude_agent.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_failing_proc(1, b"claude: some model error")),
    )
    with pytest.raises(claude_agent.ClaudeAgentError) as exc:
        await claude_agent.invoke_claude(project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava")
    assert not isinstance(exc.value, build_sandbox.BuildSandboxUnavailable)


# ---------------------------------------------------------------------------
# (h) THE PRODUCTION (streaming) PATH — every live dispatch takes it
# ---------------------------------------------------------------------------


async def test_the_streaming_path_is_sandboxed_too(monkeypatch) -> None:
    """``orchestrator.invoke_agent`` always passes ``on_event``, so a live Príprava/Návrh turn runs
    ``--output-format stream-json`` through :func:`claude_agent._invoke_streaming`. The old suite drove the
    NON-streaming branch exclusively (``grep -c on_event`` = 0), so everything added to the streaming
    handlers was untested — deleting the container reap from its timeout handler kept 103 tests green."""
    argv = await _streaming_turn_argv(monkeypatch, stage="priprava")
    opts, entrypoint_args = _scan(argv)
    assert _value_after(entrypoint_args, "--output-format") == "stream-json"
    assert "--verbose" in entrypoint_args
    assert _values(opts, "--user") == ["1000:1000"]


async def test_a_timed_out_sandbox_container_is_reaped_streaming(monkeypatch) -> None:
    """Killing the docker-run CLIENT leaves the CONTAINER running against the project — ``--rm`` only reaps
    a clean exit. On the path production actually uses."""
    proc = MagicMock()
    proc.returncode = 0
    proc.stderr = None
    proc.wait = AsyncMock(return_value=0)

    async def _never_ending():
        await asyncio.sleep(3600)
        yield b""  # pragma: no cover

    proc.stdout = _never_ending()
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(claude_agent, "_kill_process_tree", AsyncMock())
    reap = AsyncMock()
    monkeypatch.setattr(build_sandbox, "reap_container", reap)

    async def _on_event(_evt: dict) -> None:
        return None

    with pytest.raises(claude_agent.ClaudeAgentTimeout):
        await claude_agent.invoke_claude(
            project_slug="acme",
            claude_session_id=uuid4(),
            prompt="p",
            stage="priprava",
            timeout=1,
            on_event=_on_event,
        )
    reap.assert_awaited_once()
    assert reap.await_args.args[0].startswith("nex-build-")


async def test_an_unexpected_error_mid_stream_still_reaps_the_container(monkeypatch) -> None:
    """The regression :mod:`consult_sandbox` already had a test for and this module did not carry over.

    A stream line above :data:`claude_agent._STREAM_LINE_LIMIT` raises ``ValueError``/``LimitOverrunError``
    — the very case CR-NS-018 raised that limit for — which is neither a Timeout nor a Cancel. Without a
    general handler the docker-run client dies with the coroutine and the CONTAINER keeps running, holding
    WRITE access to the project after the turn is over from the backend's point of view.
    """
    proc = MagicMock()
    proc.returncode = 0

    async def _explode():
        raise ValueError("Separator is not found, and chunk exceed the limit")
        yield b""  # pragma: no cover

    proc.stdout = _explode()
    proc.stderr = None
    proc.wait = AsyncMock(return_value=0)
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(claude_agent, "_kill_process_tree", AsyncMock())
    reap = AsyncMock()
    monkeypatch.setattr(build_sandbox, "reap_container", reap)

    async def _on_event(_evt: dict) -> None:
        return None

    with pytest.raises(ValueError):
        await claude_agent.invoke_claude(
            project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava", on_event=_on_event
        )
    reap.assert_awaited_once()


async def test_an_unexpected_error_mid_run_still_reaps_the_container_non_streaming(monkeypatch) -> None:
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=OSError("broken pipe"))
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(claude_agent, "_kill_process_tree", AsyncMock())
    reap = AsyncMock()
    monkeypatch.setattr(build_sandbox, "reap_container", reap)
    with pytest.raises(OSError):
        await claude_agent.invoke_claude(project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava")
    reap.assert_awaited_once()


async def test_a_timed_out_sandbox_container_is_reaped(monkeypatch) -> None:
    # The non-streaming branch keeps its own coverage (Gate E / the consult transport use it).
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(claude_agent, "_kill_process_tree", AsyncMock())
    reap = AsyncMock()
    monkeypatch.setattr(build_sandbox, "reap_container", reap)
    with pytest.raises(claude_agent.ClaudeAgentTimeout):
        await claude_agent.invoke_claude(
            project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="priprava", timeout=1
        )
    reap.assert_awaited_once()
    assert reap.await_args.args[0].startswith("nex-build-")


# ---------------------------------------------------------------------------
# STEP 2 — Programovanie: a database INSTEAD of the docker socket
# ---------------------------------------------------------------------------


async def test_programovanie_gets_a_database_it_can_actually_reach(monkeypatch, docker_calls) -> None:
    """(a) The whole justification for sandboxing this phase, asserted end to end.

    "It has a DATABASE_URL" would be worth nothing on its own — a URL pointing at a host the container has
    no route to is exactly the failure this is meant to prevent. So the three facts are checked TOGETHER and
    against the real argv: the sandbox joins network N, the database container was created on network N, and
    the hostname inside the URL is the alias that container answers to on it.
    """
    argv = await _turn_argv(monkeypatch, stage="programovanie", slug="acme")
    opts, _ = _scan(argv)

    networks = _values(opts, "--network")
    assert len(networks) == 1, f"exactly one network, got {networks}"
    urls = [e.split("=", 1)[1] for e in _values(opts, "-e") if e.startswith("DATABASE_URL=")]
    assert len(urls) == 1, f"exactly one DATABASE_URL, got {urls}"

    created = _control(docker_calls, "docker", "network", "create")
    started = _control(docker_calls, "docker", "run")
    assert created[-1] == networks[0]
    assert started[started.index("--network") + 1] == networks[0]

    alias = started[started.index("--network-alias") + 1]
    assert f"@{alias}:{build_db.POSTGRES_PORT}/" in urls[0], urls[0]
    # …and the engine waited for it to answer before handing the turn a URL (a cold postgres needs seconds).
    assert any(c[:3] == ("docker", "exec", started[started.index("--name") + 1]) for c in docker_calls)


async def test_the_database_url_can_never_be_the_studios_own(monkeypatch) -> None:
    """Why ``DATABASE_URL`` is passed BY VALUE and not by name, as a test rather than a comment.

    ``-e DATABASE_URL`` (by name) copies the value from the docker CLI's own environment — which is the
    BACKEND's, whose ``DATABASE_URL`` is the NEX Studio production database. By-name would therefore hand
    the agent that URL the moment an override stopped being applied; by-value can only hand it the string
    the planner composed. This pins the composed one and that the ambient one never travels.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+pg8000://studio:studio@db:5432/nexstudiovisual_ambient")
    argv = await _turn_argv(monkeypatch, stage="programovanie", slug="acme")
    opts, _ = _scan(argv)
    env = _values(opts, "-e")
    assert "DATABASE_URL" not in env, "by NAME would copy the backend's own database URL into the sandbox"
    assert "nexstudiovisual_ambient" not in " ".join(argv)
    assert [e for e in env if e.startswith("DATABASE_URL=")]


async def test_a_database_buys_a_network_and_not_one_extra_path(monkeypatch) -> None:
    """(b) The socket is absent as an EQUALITY over the mount set — never a list of forbidden strings.

    The deny-list version of this file stayed green while audit mounted the whole host disk, so "no
    ``docker.sock`` in the argv" is not the assertion. The assertion is that Programovanie's mount set is
    the SAME SET as Príprava's: whatever STEP 2 handed the riskiest phase, it was not a path.
    """
    priprava, _ = _scan(await _turn_argv(monkeypatch, stage="priprava", slug="acme"))
    programovanie, _ = _scan(await _turn_argv(monkeypatch, stage="programovanie", slug="acme"))
    assert _mount_identity(programovanie) == _expected_mounts("acme")
    assert _mount_identity(programovanie) == _mount_identity(priprava)
    # The difference between the two phases is exactly: one network and one variable.
    assert _values(priprava, "--network") == []
    assert len(_values(programovanie, "--network")) == 1


def _phase_invariant_opts(opts: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """The argv options with ONLY the three things a phase is allowed to differ in removed.

    ``--name`` carries the per-turn token, ``--network`` is the per-build network and ``-e DATABASE_URL=``
    is the composed URL — everything else must be identical for every sandboxed phase, in ORDER and in
    MULTIPLICITY (a list, not a set, so a duplicated flag is a difference too — docker honours the last
    occurrence of ``--user``, which is exactly how a second one becomes a root escalation).
    """
    return [
        (flag, value)
        for flag, value in opts
        if flag not in ("--name", "--network") and not (flag == "-e" and (value or "").startswith("DATABASE_URL="))
    ]


@pytest.mark.parametrize("stage", [s for s in build_sandbox.SANDBOXED_PHASES if s != "priprava"])
async def test_every_sandboxed_phase_gets_the_same_argv_but_its_database(monkeypatch, stage) -> None:
    """THE WHOLE ARGV, phase against phase — the assertion the per-branch mutations walked straight past.

    ``test_a_database_buys_a_network_and_not_one_extra_path`` compares the MOUNT SET and counts networks,
    and its own comment claims "the difference between the two phases is exactly: one network and one
    variable" — but it never compared the other options, so ``build_run_argv``'s ``if database is not None:``
    branch was outside every equality in this file. Audit put ``--user 0:0`` there (root, since docker
    honours the last one) and then ``-e NEX_MASTER_KEY``; both left the suite green at 112 passed, and both
    are red here.

    Stated as "identical except for the three per-turn/per-phase values" rather than as another hand-written
    list, because a list is a thing that drifts: whatever is added to that branch tomorrow — a mount, a
    capability, an environment variable, a second ``--user`` — is a difference this test names.
    """
    priprava, _ = _scan(await _turn_argv(monkeypatch, stage="priprava", slug="acme"))
    other, _ = _scan(await _turn_argv(monkeypatch, stage=stage, slug="acme"))
    assert _phase_invariant_opts(other) == _phase_invariant_opts(priprava)


async def test_the_database_container_is_not_reachable_from_anywhere_else(monkeypatch, docker_calls) -> None:
    """The per-build database publishes NO port and mounts NO volume.

    A published port would put an empty, freshly-passworded PostgreSQL on the host's interfaces for the
    length of every build; a volume would let one build's test data survive into the next one. Neither is a
    hypothetical addition — both are one line in a ``docker run``.
    """
    await _turn_argv(monkeypatch, stage="programovanie", slug="acme")
    started = _control(docker_calls, "docker", "run")
    assert "-p" not in started and "--publish" not in started
    assert "-v" not in started and "--volume" not in started and "--mount" not in started
    assert "--network" in started  # …and it is on the private per-build network, not the default bridge


async def test_a_project_needing_more_than_postgres_fails_loudly_and_names_it(monkeypatch, tmp_path) -> None:
    """(d) An admitted boundary, never a silent half-environment.

    A build turn given half its services produces failures nobody can attribute — the agent "fixes" code
    that was never broken. So the turn dies before anything starts, and the message carries the name of the
    service the engine did not supply, because "unsupported service" would send somebody reading YAML.
    """
    root = tmp_path / "projects"
    (root / "acme").mkdir(parents=True)
    (root / "acme" / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n    image: postgres:16-alpine\n"
        "  cache:\n    image: redis:7-alpine\n"
        "  backend:\n    build: ./backend\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(build_sandbox, "_CONTAINER_TO_HOST_PREFIX", ((str(root), str(root)),))
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)

    with pytest.raises(build_db.UnsupportedProjectService) as exc:
        await claude_agent.invoke_claude(
            project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="programovanie"
        )
    assert "cache" in str(exc.value)
    # The project's OWN services are not mistaken for infrastructure — only the off-the-shelf one is named.
    assert "backend" not in str(exc.value)
    mock_exec.assert_not_called()


@pytest.mark.parametrize("outcome", ["clean", "crash", "timeout"])
async def test_the_database_and_its_network_die_with_the_turn(monkeypatch, docker_calls, outcome) -> None:
    """(c) Cleanup on EVERY exit path — the leak nobody notices until the disk is full.

    ``--rm`` cannot do this job: the database has to outlive the docker CLI that created it, and on a
    timeout that CLI is killed anyway. So the release runs from a ``finally``, and all three shapes of exit
    are driven here rather than trusting that one implies the others.
    """
    if outcome == "clean":
        proc = _ok_proc()
    elif outcome == "crash":
        proc = _failing_proc(1, b"claude: some model error")
    else:
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(claude_agent, "_kill_process_tree", AsyncMock())
    monkeypatch.setattr(build_sandbox, "reap_container", AsyncMock())

    async def _run() -> None:
        await claude_agent.invoke_claude(
            project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="programovanie", timeout=1
        )

    if outcome == "clean":
        await _run()
    else:
        with pytest.raises(claude_agent.ClaudeAgentError):
            await _run()

    started = _control(docker_calls, "docker", "run")
    container = started[started.index("--name") + 1]
    network = started[started.index("--network") + 1]
    assert ("docker", "rm", "-f", container) in docker_calls
    assert ("docker", "network", "rm", network) in docker_calls


async def test_a_cancelled_dispatch_takes_its_database_with_it(monkeypatch, docker_calls) -> None:
    """The fourth exit path, and the one a ``finally`` exists for: the coroutine is cancelled mid-turn."""
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(claude_agent, "_kill_process_tree", AsyncMock())
    monkeypatch.setattr(build_sandbox, "reap_container", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await claude_agent.invoke_claude(
            project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="programovanie"
        )
    started = _control(docker_calls, "docker", "run")
    assert ("docker", "rm", "-f", started[started.index("--name") + 1]) in docker_calls
    assert ("docker", "network", "rm", started[started.index("--network") + 1]) in docker_calls


async def test_a_failure_between_the_database_and_the_container_leaks_neither(monkeypatch, docker_calls) -> None:
    """THE FIFTH EXIT PATH — the one the ``finally`` did not cover, because it was not yet open.

    ``_invoke_once`` used to run ``build_db.start`` and then compose the sandbox argv BEFORE the ``try``:

        642  await build_db.start(turn_database)      ← network + PostgreSQL now running
        643  args = build_sandbox.build_run_argv(…)   ← declares Raises: BuildSandboxUnavailable
        650  try: … 664 finally: release(…)

    So every way ``build_run_argv`` can refuse — an empty claude argv, an unreachable session dir, a mount
    it cannot compose — left a PostgreSQL and a per-build network running with nothing to remove them.
    ``--rm`` cannot save it: the container must OUTLIVE the docker CLI that created it, which is the whole
    reason ``release`` exists. The other four exit paths (clean/crash/timeout/cancel) all run AFTER the argv
    is built, so none of them touched this and the module's claim that the database dies with the turn "on
    every path out" was simply untrue.
    """
    monkeypatch.setattr(
        build_sandbox,
        "build_run_argv",
        MagicMock(side_effect=build_sandbox.BuildSandboxUnavailable("composing the argv failed")),
    )
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)

    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        await claude_agent.invoke_claude(
            project_slug="acme", claude_session_id=uuid4(), prompt="p", stage="programovanie"
        )

    mock_exec.assert_not_called()  # no turn ever ran…
    started = _control(docker_calls, "docker", "run")  # …but the database HAD been started
    assert ("docker", "rm", "-f", started[started.index("--name") + 1]) in docker_calls
    assert ("docker", "network", "rm", started[started.index("--network") + 1]) in docker_calls


async def test_two_concurrent_builds_cannot_collide(monkeypatch, docker_calls) -> None:
    """(f) Two builds of the SAME project at the same moment must not share a name — of anything.

    The worst version of this bug is not a crash: it is the second build attaching to the first build's
    database and the two test suites truncating each other's tables, which reads as flaky tests forever.
    Every docker object of a turn is therefore named after the project AND a per-turn token.
    """
    first = await _turn_argv(monkeypatch, stage="programovanie", slug="acme")
    calls_first = list(docker_calls)
    docker_calls.clear()
    second = await _turn_argv(monkeypatch, stage="programovanie", slug="acme")

    def _names(argv: list[str], calls: list[tuple[str, ...]]) -> tuple[str, str, str]:
        opts, _ = _scan(argv)
        run = _control(calls, "docker", "run")
        return (
            _values(opts, "--name")[0],
            run[run.index("--name") + 1],
            _control(calls, "docker", "network", "create")[-1],
        )

    a = _names(first, calls_first)
    b = _names(second, list(docker_calls))
    assert not set(a) & set(b), f"two concurrent builds share a docker object name: {set(a) & set(b)}"
    # …and the project is still IN each name, so an operator can find a leak by project.
    assert all("acme" in name for name in a + b)


def test_a_turn_names_all_three_objects_after_one_token(monkeypatch) -> None:
    """One token per TURN, not one per object — so everything a leaked dispatch left behind is traceable
    to the same dispatch instead of being three unrelated names."""
    token = build_sandbox.turn_token()
    plan = build_db.plan_database(slug="acme", host_project_dir="/nonexistent", token=token)
    assert build_sandbox.container_name("acme", token).endswith(token)
    assert plan.container.endswith(token)
    assert plan.network.endswith(token)


def test_only_programovanie_is_given_a_database(monkeypatch) -> None:
    """A database for a phase that never queries one is a container nobody reaps."""
    token = "0123456789ab"
    for stage in ("priprava", "navrh", "vizual", "verifikacia", None, ""):
        assert build_sandbox.plan_turn_database(project_slug="acme", stage=stage, token=token) is None
    assert build_sandbox.plan_turn_database(project_slug="acme", stage="programovanie", token=token) is not None


async def test_the_kill_switch_also_switches_the_database_off(monkeypatch, docker_calls) -> None:
    """(7) One lever, not two. ``BUILD_SANDBOX=0`` sends Programovanie back to the subprocess — where it has
    the docker socket and starts its own database — so the engine must not also be starting one."""
    monkeypatch.setenv("BUILD_SANDBOX", "0")
    assert (await _turn_argv(monkeypatch, stage="programovanie"))[0] == "claude"
    assert docker_calls == []


# ---------------------------------------------------------------------------
# Readiness — an unmet precondition is visible at boot, not at the first stalled build
# ---------------------------------------------------------------------------


def test_preflight_names_the_missing_image(monkeypatch) -> None:
    monkeypatch.setattr(build_sandbox.shutil, "which", lambda _n: "/usr/bin/docker")
    monkeypatch.setattr(
        build_sandbox.subprocess,
        "run",
        lambda *a, **k: MagicMock(returncode=1, stdout="", stderr="Error: No such image: x"),
    )
    status = build_sandbox.preflight()
    assert status.enabled is True
    assert status.ready is False
    assert "not available on this host" in " ".join(status.problems)
    assert "BUILD_SANDBOX=0" in " ".join(status.problems)


def test_preflight_names_a_missing_subscription_token(monkeypatch) -> None:
    """With an ephemeral HOME there is no mounted ``.credentials.json`` any more: the env token is the ONLY
    authentication the sandbox has, so an unset one is a boot-time readiness problem, not something for the
    first Príprava turn to discover."""
    monkeypatch.setattr(build_sandbox.shutil, "which", lambda _n: "/usr/bin/docker")
    monkeypatch.setattr(
        build_sandbox.subprocess, "run", lambda *a, **k: MagicMock(returncode=0, stdout="id", stderr="")
    )
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    status = build_sandbox.preflight()
    assert status.ready is False
    assert "CLAUDE_CODE_OAUTH_TOKEN" in " ".join(status.problems)


def test_preflight_reports_the_kill_switch_as_a_choice_not_a_fault(monkeypatch) -> None:
    monkeypatch.setenv("BUILD_SANDBOX", "0")
    status = build_sandbox.preflight()
    assert status.enabled is False
    assert status.ready is False  # the guarantee genuinely is not in effect
    assert "switched off" in " ".join(status.problems)


def test_the_network_residual_is_published_not_implied() -> None:
    """What the sandbox does NOT isolate has to be readable, not inferred from silence — AND IT HAS TO BE
    THE WHOLE ANSWER.

    The published sentence used to name three services (the Studio API, the v3 API, Traefik) and add that
    "Postgres, Ollama and Qdrant were NOT reachable". Audit re-measured from a container on a user-defined
    network of the exact shape ``build_db.network_create_argv`` makes, and the truth is categorical: the
    bridge gateway answers on EVERY port published to 0.0.0.0 on this host — six PostgreSQL instances (this
    Studio's own production database among them), Vaultwarden, the NEX Manager API, Prometheus, Alertmanager
    and Grafana included. A short list where the rule is "everything published" reads as a reassurance, so
    this test pins the CATEGORY and the databases by name; naming three ports again turns it red.

    ``--network none`` is not an option — it cuts the Claude MAX endpoint too and the turn hangs until its
    timeout, measured. The fix is a DOCKER-USER firewall rule on the host, which is infrastructure.
    """
    payload = build_sandbox.preflight().as_dict()
    assert "network_residual" in payload
    residual = payload["network_residual"].lower()
    for expected in ("published on 0.0.0.0", "not a short list", "postgresql", "not implemented"):
        assert expected in residual, f"{expected!r} missing from the published network residual"
    # The honest half in the sandbox's favour is stated too: other stacks' own networks are NOT reachable.
    assert "private networks are not reachable" in residual


def test_reading_the_knowledge_base_works_BOTH_ways_or_neither(monkeypatch) -> None:
    """Director 23.08.2026: *"pripoj znalostnú bázu read-only"* — and reading means BOTH halves of §3(1).

    Charter §3(1) says the access is *"RAG (Qdrant + Ollama embeddings) + priame čítanie súborov"*. Mounting
    the files alone would have left the agent with a knowledge base it can open but not search, because the
    project's own ``scripts/rag_query.py`` talks to Qdrant directly — a half-working §3 is the same trap in
    a smaller size. So this pins BOTH, and pins the writes as still refused: read-only was the decision.
    """
    doc = build_sandbox.__doc__ or ""
    # RAG half — reachable.
    assert "QDRANT_URL" in build_sandbox._PASSTHROUGH_ENV
    assert "OLLAMA_URL" in build_sandbox._PASSTHROUGH_ENV
    # File half — mounted, and mounted READ-ONLY (the mount-equality test asserts the readonly flag).
    assert build_sandbox._KB_DIR == "/home/icc/knowledge"
    # §3(3) stays refused, and the docstring must say so rather than let it be discovered by a failed write.
    assert "read-only" in doc.lower()
    assert "MEMORY.md" in doc


def test_the_two_transports_of_a_consult_turn_cannot_drift_apart(monkeypatch) -> None:
    """The failure that made this a rule: ``claude_agent._invoke_once`` passed ``settings_path=`` to
    ``consult_sandbox.run_consult_in_sandbox``, whose signature did not have it. Every live consult raised
    ``TypeError`` — which ``except SidecarUnavailable`` does not catch, so the turn died AND
    ``record_degradation`` never ran, leaving ``/health``'s ``degraded_turns`` at 0 for a guarantee that was
    failing every single time. A signature is checkable; leave it to review and it drifts again."""
    from backend.services import consult_sandbox

    sidecar = set(inspect.signature(consult_sandbox.run_consult_in_sandbox).parameters)
    in_process = set(inspect.signature(claude_agent._invoke_once).parameters)
    # Everything the sidecar transport is handed must be a parameter it declares.
    handed_over = {
        "project_slug",
        "claude_session_id",
        "prompt",
        "charter_path",
        "timeout",
        "model",
        "effort",
        "json_schema",
        "allowed_tools",
        "settings_path",
    }
    assert handed_over <= sidecar, f"the consult sidecar cannot accept {handed_over - sidecar}"
    assert handed_over <= in_process


def test_the_readiness_line_counts_instead_of_claiming(monkeypatch) -> None:
    """The startup disclosure must DERIVE how many phases are isolated (ICCINT-20).

    It announced "three phases of five" for the whole of the release that made it four — in the same
    sentence that listed all four by name. That line is the one statement anybody reads to learn what is
    isolated, so a hand-written number beside a generated list is exactly the wrong thing to put there.

    Asserted on the RENDERED message, not on the source: what matters is what the operator reads.
    """
    monkeypatch.setattr(
        build_sandbox,
        "preflight",
        lambda: build_sandbox.SandboxStatus(enabled=True, ready=True, image="img:v1"),
    )
    # Capture by attaching a handler to the module's own logger. ``caplog`` depends on propagation to the
    # root, which this project's logging config does not grant — and a capture that silently yields nothing
    # would make every assertion below pass against an empty string.
    captured: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    mod_logger = logging.getLogger("backend.services.build_sandbox")
    handler = _Grab()
    mod_logger.addHandler(handler)
    try:
        build_sandbox.log_startup_readiness()
    finally:
        mod_logger.removeHandler(handler)
    line = "\n".join(captured)
    assert line, "the readiness line was never emitted — the assertions below would be vacuous"

    assert "three phases of five" not in line
    assert "4 of 5 phases" in line
    # Every isolated phase is named, and the one left out is not claimed to be in.
    for phase in build_sandbox.SANDBOXED_PHASES:
        assert phase in line
    assert set(build_sandbox.SANDBOXED_PHASES) < set(build_sandbox.ALL_BUILD_PHASES)
    assert "verifikacia" not in build_sandbox.SANDBOXED_PHASES
