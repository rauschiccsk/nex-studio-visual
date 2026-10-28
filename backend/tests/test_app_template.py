"""DEV-253 — founding a project on an app template: the newest nex-app-kit, its scaffold filled with the project's
values over the folder init.sh made, the frontend's lockfile, one commit — and a sentence when any of it fails."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.services import app_template
from backend.services.app_template import AppTemplateError, TemplateChoice

TAGS = "\n".join(
    [
        "aaa\trefs/tags/v0.9.0",
        "bbb\trefs/tags/v0.10.0",
        "bbb\trefs/tags/v0.10.0^{}",
        "ccc\trefs/tags/v0.2.0",
        "ddd\trefs/tags/pokus",
    ]
)


class FakeRunner:
    """The outside world of the founding: git, the renderer, npm — answered from a table, every call recorded."""

    def __init__(self, fail: str | None = None, tags: str = TAGS) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.values: dict | None = None
        self.fail = fail
        self.tags = tags

    def __call__(self, argv: list[str], cwd: str | None = None, **_kwargs) -> subprocess.CompletedProcess:
        self.calls.append((argv, cwd))
        what = " ".join(argv[:2])
        if argv[0] == "python3":
            self.values = json.loads(Path(argv[argv.index("--values") + 1]).read_text("utf-8"))
        if self.fail and self.fail in " ".join(argv[:2]):
            return subprocess.CompletedProcess(argv, 1, "", "Prihlásenie je „password“ alebo „manager“, nie „x“.")
        out = self.tags if what == "git ls-remote" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


def no_fetch(_version, destination: Path, _run) -> None:
    destination.mkdir(parents=True)


def founded(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def apply(target: Path, run: FakeRunner, **changes):
    args = dict(
        name="NEX Knowledge",
        slug="nex-knowledge",
        auth_mode="token",
        ports=(10260, 10261, 10262),
        run=run,
        fetch_kit=no_fetch,
    )
    args.update(changes)
    return app_template.apply_template(target, TemplateChoice("nsv-standard", ai=True), **args)


def test_the_newest_release_of_the_kit_is_taken():
    assert app_template.latest_kit_version(FakeRunner()) == "0.10.0"


@pytest.mark.parametrize(
    "runner,sentence",
    [
        (FakeRunner(tags="ddd\trefs/tags/pokus"), "Šablóna aplikácie nex-app-kit nemá žiadnu vydanú verziu."),
        (
            FakeRunner(fail="ls-remote"),
            "Šablónu aplikácie sa nepodarilo načítať — GitHub neodpovedal na zoznam verzií nex-app-kit.",
        ),
    ],
)
def test_no_release_to_take_is_said(runner, sentence):
    with pytest.raises(AppTemplateError) as caught:
        app_template.latest_kit_version(runner)
    assert str(caught.value) == sentence


@pytest.mark.parametrize(
    "name,initials",
    [
        ("NEX Knowledge", "NK"),
        ("NEX Studio Visual", "NSV"),
        ("Career Asistent", "CA"),
        ("Ukážka", "UK"),
        ("a b c d e", "ABCD"),
        ("nex-inbox", "NI"),
    ],
)
def test_initials_come_from_the_name(name, initials):
    assert app_template.initials_of(name) == initials


def test_the_project_s_choices_become_the_scaffold_s_values():
    values = app_template.scaffold_values(
        name="NEX Knowledge", slug="nex-knowledge", auth_mode="token", ai=True, ports=(1, 2, 3), version="0.3.0"
    )

    assert values == {
        "app_name": "NEX Knowledge",
        "app_initials": "NK",
        "slug": "nex-knowledge",
        "auth": "manager",
        "ai": True,
        "backend_port": 1,
        "frontend_port": 2,
        "db_port": 3,
        "kit_frontend": "github:rauschiccsk/nex-app-kit#v0.3.0",
        "kit_server": "nex-app-kit @ https://github.com/rauschiccsk/nex-app-kit/archive/refs/tags/v0.3.0.tar.gz"
        "#subdirectory=server",
    }
    assert (
        app_template.scaffold_values(name="A", slug="a", auth_mode="password", ai=False, ports=(1, 2, 3), version="1")[
            "auth"
        ]
        == "password"
    )


def test_the_template_is_filled_over_the_project_then_locked_and_committed(tmp_path):
    target = founded(tmp_path)
    run = FakeRunner()

    version = apply(target, run)

    assert version == "0.10.0"
    steps = [(argv[0], argv[1] if len(argv) > 1 else "", cwd) for argv, cwd in run.calls]
    assert steps == [
        ("git", "ls-remote", None),
        ("python3", steps[1][1], None),
        ("npm", "install", str(target / "frontend")),
        ("git", "add", str(target)),
        ("git", "commit", str(target)),
    ]
    render = run.calls[1][0]
    assert render[1].endswith("scaffold/render.py") and render[2:3] == ["nsv-standard"]
    assert render[render.index("--into") + 1] == str(target)
    assert run.values["auth"] == "manager" and run.values["ai"] is True
    assert run.values["kit_frontend"] == "github:rauschiccsk/nex-app-kit#v0.10.0"
    assert run.calls[3][0][-5:] == ["frontend", "backend", "docker-compose.yml", "docs/AKO_ZACAT.md", ".env.example"]
    assert "(nex-app-kit v0.10.0)" in run.calls[4][0][-1]


def test_without_a_checkout_there_is_nothing_to_fill(tmp_path):
    run = FakeRunner()

    assert apply(tmp_path / "nie-je", run) is None
    assert run.calls == []


@pytest.mark.parametrize(
    "fails,sentence",
    [
        ("render.py", "Kostru šablóny sa nepodarilo vyplniť: Prihlásenie je „password“ alebo „manager“, nie „x“."),
        ("npm", "Frontendu šablóny sa nepodarilo pripraviť zoznam balíkov (npm install)."),
        ("commit", "Kostru šablóny sa nepodarilo zapísať do gitu projektu."),
    ],
)
def test_a_failed_step_is_said(tmp_path, fails, sentence):
    with pytest.raises(AppTemplateError) as caught:
        apply(founded(tmp_path), FakeRunner(fail=fails))
    assert str(caught.value) == sentence


def test_a_kit_release_that_cannot_be_fetched_is_said(tmp_path):
    def broken_fetch(version, destination, run):
        app_template._fetch_kit(version, destination, run)

    with pytest.raises(AppTemplateError) as caught:
        apply(founded(tmp_path), FakeRunner(fail="clone"), fetch_kit=broken_fetch)
    assert str(caught.value) == "Šablónu aplikácie (nex-app-kit v0.10.0) sa nepodarilo stiahnuť z GitHubu."


def test_an_unknown_template_is_said_before_anything_is_fetched(tmp_path):
    run = FakeRunner()
    with pytest.raises(AppTemplateError) as caught:
        app_template.apply_template(
            founded(tmp_path),
            TemplateChoice("iny-vzhlad", ai=False),
            name="A",
            slug="a",
            auth_mode="password",
            ports=(1, 2, 3),
            run=run,
            fetch_kit=no_fetch,
        )
    assert str(caught.value) == "Šablóna „iny-vzhlad“ neexistuje."
    assert run.calls == []
