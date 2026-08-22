"""``python -m backend.cli.dedo_unblock`` — release a build stuck on a NEX Studio bug (ICCINT-13).

When the AI Agent escalates a bug that can only be fixed in NEX Studio itself, the build settles
``blocked`` / ``block_reason='framework_issue'`` and stops. Once the technical team has actually fixed it,
THIS is what lets the build go on. The Manažér has no such command and no such button, on purpose: he cannot
tell whether NEX Studio was really repaired, and releasing the build into the same broken version would only
burn another round. He decides the step AFTER this one — the build lands back on his desk with a single
"Pokračovať" button.

Deliberately THIN. All of the meaning lives in :mod:`backend.services.dedo_unblock`; this module parses
arguments, opens a session and commits. No HTTP, no login: charter §4.5 keeps Dedo out of the API until he
has his own machine identity (ICCINT-14), and when that endpoint arrives it must call the SAME
:func:`~backend.services.dedo_unblock.unblock_framework_issue`.

Usage::

    python -m backend.cli.dedo_unblock --project nex-inbox --reason "Opravené vo v4.0.58 — port sa už nezamyká."
    python -m backend.cli.dedo_unblock --version-id <uuid> --reason "..."
    ... | python -m backend.cli.dedo_unblock --project nex-inbox        # reason on stdin

The reason is MANDATORY. It is written into the build's thread as Dedo's own message (``author='dedo'``), so
there is a permanent record of who let the build go on and why — and the agent reads it as the answer to its
escalation on the very next turn, which means the usual case needs only this one command. Use
``python -m backend.cli.dedo_message`` first when the answer needs more detail than the reason line.

With ``--project`` the build is resolved to the project's one build blocked on ``framework_issue``; the
command REFUSES an ambiguous target rather than picking one. ``--version-id`` names a build directly.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from backend.db.session import SessionLocal
from backend.services import dedo_unblock as dedo_unblock_service


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli.dedo_unblock",
        description="Release a build blocked on a NEX Studio bug (framework_issue) after the fix has landed.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--version-id", help="UUID of the version to release")
    target.add_argument(
        "--project",
        help="Project slug — resolves to that project's build blocked on framework_issue (refuses if ambiguous)",
    )
    parser.add_argument(
        "-r",
        "--reason",
        help="Why the build may go on (what was fixed). Required — omit to read it from stdin.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Release one build; return a process exit code (0 = released, 2 = refused)."""
    args = _build_parser().parse_args(argv)

    reason = args.reason if args.reason is not None else sys.stdin.read()
    if not (reason or "").strip():
        print("Prázdny dôvod — stavba sa neodblokovala. Napíš, čo bolo opravené.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        if args.version_id:
            try:
                version_id = uuid.UUID(args.version_id)
            except ValueError:
                print(f"--version-id nie je platné UUID: {args.version_id}", file=sys.stderr)
                return 2
        else:
            version_id = dedo_unblock_service.version_awaiting_dedo(db, args.project).id

        dedo_unblock_service.unblock_framework_issue(db, version_id=version_id, reason=reason)
        db.commit()
    except dedo_unblock_service.DedoMessageError as exc:
        db.rollback()
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Stavba odblokovaná (verzia {version_id}).")
    print(
        "Manažér teraz vidí v kokpite jediné tlačidlo „Pokračovať“. Ťah, ktorý ním spustí, nesie tvoju "
        "odpoveď agentovi — stavba beží ďalej až po jeho kliknutí.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — process entry point
    raise SystemExit(main())
