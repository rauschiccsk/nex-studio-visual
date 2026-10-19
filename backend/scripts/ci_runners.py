"""Brings the CI runners of the cockpit's projects to the pair with a Docker of their own (DEV-145).

Without ``--apply`` only prints which runner each project has. ``--apply`` converts every runner that is not current
— or only the projects named — but never one that is running a job: recreating it would kill the job. Runs in the
cockpit backend container, which drives the server's Docker::

    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.ci_runners
    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.ci_runners --apply nex-websites

The work itself is :mod:`backend.services.ci_runner`; founding a project uses the same.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.db.session import SessionLocal
from backend.services import ci_runner, github_validation
from backend.services.template_bootstrap import _repo_from_url, resolve_github_org

STATE_TEXT = {
    ci_runner.NONE: "bez bežca (CI beží inde alebo ho projekt nemá)",
    ci_runner.CURRENT: "bežec s vlastným Dockerom, aktuálny",
    ci_runner.OUTDATED: "bežec s vlastným Dockerom zo staršieho predpisu — previesť",
    ci_runner.LEGACY: "bežec s ostrým Dockerom servera — previesť",
}
#: GitHub holds the session of a replaced runner for up to ~3 minutes (measured 19.10.2026).
ONLINE_WAIT = 300
ONLINE_POLL = 5


def main(argv: list[str] | None = None, *, projects: Callable[[], list[tuple[str, str]]] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prevod bežcov CI projektov na bežca s vlastným Dockerom.")
    parser.add_argument("--apply", action="store_true", help="previesť (bez neho len výpis)")
    parser.add_argument("slugs", nargs="*", metavar="projekt", help="len tieto projekty (bez nich všetky)")
    args = parser.parse_args(argv)

    found = (projects or _projects_from_db)()
    unknown = sorted(set(args.slugs) - {slug for slug, _ in found})
    if unknown:
        print(f"kokpit taký projekt nemá: {', '.join(unknown)}")
        return 2
    states = {slug: ci_runner.state(slug) for slug, _ in found}
    for slug, _ in found:
        print(f"{slug}: {STATE_TEXT[states[slug]]}")
    chosen = [
        (slug, repo_full)
        for slug, repo_full in found
        if (not args.slugs or slug in args.slugs) and states[slug] in (ci_runner.OUTDATED, ci_runner.LEGACY)
    ]
    if not args.apply:
        print(f"NANEČISTO — nič sa nezmenilo; previesť treba {len(chosen)}. Prevod: --apply [projekt …]")
        return 0

    failed = 0
    for slug, repo_full in chosen:
        label = f"andros-ubuntu-{slug}"
        try:
            if github_validation.runner_busy(repo_full, label, timeout=ci_runner.GITHUB_TIMEOUT):
                print(f"{slug}: práve robí úlohu CI — nechaný tak; prevod spusti znovu po jej skončení")
                failed += 1
                continue
            ci_runner.provision(slug, repo_full)
        except Exception as exc:  # noqa: BLE001 — one project's failure is reported, the others still convert
            print(f"{slug}: prevod zlyhal — {exc}")
            failed += 1
            continue
        status = _wait_online(repo_full, label)
        print(f"{slug}: prevedený — {STATE_TEXT[ci_runner.state(slug)]}; na GitHube {status}")
        failed += status != "online"
    return 1 if failed else 0


def _wait_online(repo_full: str, label: str) -> str:
    deadline = time.monotonic() + ONLINE_WAIT
    while True:
        try:
            status = github_validation.runner_status(repo_full, label, timeout=ci_runner.GITHUB_TIMEOUT)
        except RuntimeError as exc:
            return f"stav sa nedal zistiť ({exc})"
        if status == "online" or time.monotonic() >= deadline:
            return status or "neprihlásený"
        time.sleep(ONLINE_POLL)


def projects_in(db: Session) -> list[tuple[str, str]]:
    """Every project of the cockpit as ``(slug, owner/repository)`` — the repository its runner registers against."""
    owner = resolve_github_org(db)
    rows = db.query(Project.slug, Project.repo_url).order_by(Project.slug).all()
    return [(slug, _repo_from_url(repo_url, slug, default_owner=owner)) for slug, repo_url in rows]


def _projects_from_db() -> list[tuple[str, str]]:
    with SessionLocal() as db:
        return projects_in(db)


if __name__ == "__main__":
    raise SystemExit(main())
