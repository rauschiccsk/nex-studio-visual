"""Write the build buttons' plain names for the frontend (DEV-373).

The cockpit names every build button in one place (``backend/services/action_labels.py``); the board carries the
names for the state the build is in. This file gives the screen the same plain names where no board has them yet
(a component drawn before its board, a test) — written by ``npm run codegen``, kept equal by the drift gate.

Usage::

    poetry run python -m backend.scripts.dump_action_labels [OUTPUT_PATH]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.services.action_labels import BASE


def render() -> str:
    return json.dumps(BASE, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if len(argv) > 1 and argv[1] != "-":
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
