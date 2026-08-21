"""Branch protection must not fail in silence.

The Manažér ticks „Chrániť hlavnú vetvu" and the create answers 201. When GitHub refuses,
the old code logged a warning and returned — so `main` stayed open to force-push and nobody
was told. On nex-productcatalogs (21.08.2026) GitHub answered 403 „Upgrade to GitHub Pro or
make this repository public" and the failure sat in the log until someone went looking.

A best-effort step is allowed to fail. It is not allowed to fail invisibly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import create_project_postscaffold as pss


def _run(monkeypatch: pytest.MonkeyPatch, *, returncode: int, stderr: str = "") -> None:
    monkeypatch.setattr(
        pss.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout="", stderr=stderr),
    )


def test_success_returns_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _run(monkeypatch, returncode=0)
    assert pss._enable_branch_protection("https://github.com/o/r", "r") is None


def test_free_plan_refusal_names_the_actual_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub refuses protection on a private repo under the free plan. That is not a
    transient error — the message must say what the options are, not echo the API."""
    _run(
        monkeypatch,
        returncode=1,
        stderr="gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)",
    )
    warning = pss._enable_branch_protection("https://github.com/rauschiccsk/x", "x")
    assert warning is not None
    assert "NEZAPLA" in warning
    assert "zverejni" in warning
    assert "GitHub Pro" in warning


def test_other_failure_still_warns_and_quotes_github(monkeypatch: pytest.MonkeyPatch) -> None:
    _run(monkeypatch, returncode=1, stderr="HTTP 422: Invalid request")
    warning = pss._enable_branch_protection("https://github.com/o/r", "r")
    assert warning is not None
    assert "NEZAPLA" in warning
    assert "422" in warning


def test_failure_without_stderr_still_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent non-zero exit must not produce a blank, meaningless notice."""
    _run(monkeypatch, returncode=1, stderr="")
    warning = pss._enable_branch_protection("https://github.com/o/r", "r")
    assert warning is not None
    assert "bez správy" in warning


def test_warning_reaches_setup_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The whole point: the notice must travel out of the step collector to the caller,
    which puts it in the create response."""
    monkeypatch.setattr(pss, "_enable_branch_protection", lambda *a, **kw: "Ochrana hlavnej vetvy sa NEZAPLA — test")
    warnings = pss.run_post_scaffold_steps(
        target=str(tmp_path),
        slug="x",
        repo_url="https://github.com/o/x",
        project_type="standard",
        auth_mode="token",
        enable_cicd=False,
        full_smoke=False,
        enable_branch_protection=True,
        github_org="o",
    )
    assert any("NEZAPLA" in w for w in warnings), warnings
