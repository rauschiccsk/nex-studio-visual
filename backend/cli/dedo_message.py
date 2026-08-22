"""``python -m backend.cli.dedo_message`` — write a message from Dedo to a build's AI Agent (ICCINT-12).

The AI Agent can escalate to Dedo (``block_reason='framework_issue'``). This is the way back, and until
ICCINT-14 gives Dedo his own machine identity it is the ONLY way back: a host-side command, no HTTP, no
login. Charter §4.5 is explicit that Dedo may not authenticate to the API until that identity exists, and
the boundary is meant to be enforced by the token, not by the caller's discipline — so this tool does not
speak to the API at all; it writes through the service layer against the same database.

Deliberately THIN. All of the meaning lives in :mod:`backend.services.dedo_message`; this module only
parses arguments, opens a session and commits. When the ICCINT-14 endpoint arrives it must call the SAME
:func:`~backend.services.dedo_message.record_dedo_message`, so there is one way for Dedo to speak.

Usage::

    python -m backend.cli.dedo_message --project nex-inbox --message "Opravené vo v4.0.58, skús znova."
    python -m backend.cli.dedo_message --version-id <uuid> --message "..."
    ... | python -m backend.cli.dedo_message --project nex-inbox      # message on stdin

With ``--project`` the build is resolved to the project's one build blocked on ``framework_issue`` — what
Dedo is answering. The command REFUSES an ambiguous target rather than picking one; ``--version-id`` names
a build directly.

This does NOT unblock the build — that is ``python -m backend.cli.dedo_unblock`` (ICCINT-13), and until it
runs, the message written here waits: a blocked build dispatches no turn to carry it. Because the unblock
records ITS reason the same way, one command is usually enough; reach for this one when the answer to the
agent needs more than the reason line.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from backend.db.session import SessionLocal
from backend.services import dedo_message as dedo_message_service


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli.dedo_message",
        description="Record a message from Dedo (the NEX Studio technical team) to a build's AI Agent.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--version-id", help="UUID of the version whose AI Agent should receive the message")
    target.add_argument(
        "--project",
        help="Project slug — resolves to that project's build blocked on framework_issue (refuses if ambiguous)",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="The message text. Omit to read it from stdin.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Record one Dedo message; return a process exit code (0 = written, 2 = refused)."""
    args = _build_parser().parse_args(argv)

    content = args.message if args.message is not None else sys.stdin.read()
    if not (content or "").strip():
        print("Prázdna správa — nič sa neodoslalo.", file=sys.stderr)
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
            version_id = dedo_message_service.version_awaiting_dedo(db, args.project).id

        msg = dedo_message_service.record_dedo_message(db, version_id=version_id, content=content)
        db.commit()
        # Read the id HERE, while the row is still attached. The confirmation below prints after
        # ``finally: db.close()`` has detached it, and a detached-instance attribute read raises. It happens
        # to work today only because ``SessionLocal`` is built with ``expire_on_commit=False``; the operator's
        # one receipt for "the message went in" must not hinge on that setting.
        msg_id = msg.id
    except dedo_message_service.DedoMessageError as exc:
        db.rollback()
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Správa od Deda zapísaná (verzia {version_id}, správa {msg_id}).")
    print(
        "Agent ju dostane pri NAJBLIŽŠOM svojom ťahu. POZOR: samotný zápis build neodblokuje — build "
        "zablokovaný na chybe NEX Studia nespustí žiadny ťah, takže správa dovtedy čaká v poradí. Keď je "
        "chyba naozaj opravená, odblokuj ho: python -m backend.cli.dedo_unblock --project <slug> "
        '--reason "<čo bolo opravené>".',
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — process entry point
    raise SystemExit(main())
