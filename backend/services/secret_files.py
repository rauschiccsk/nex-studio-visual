"""Which files of a project carry secrets — one rule for every reader that shows a project's files to somebody.

Poradca overlays them with an empty file in its container (:func:`backend.services.poradca.sandbox.overlay_paths`);
NS Code (DEV-86) never lists nor serves them. Kept in one place so the two can never disagree about what a secret
is — a second copy of the pattern would be the one somebody forgets to extend.
"""

from __future__ import annotations

import re

#: Names of files that carry secrets, wherever in the project they lie.
SECRET_FILE_RE = re.compile(
    r"^(?:\.env(?:\..+)?"
    r"|.+\.(?:pem|key|p12|pfx|jks|keystore|kdbx)"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:[^.]*|.*(?<!\.pub))"
    r"|\.netrc|\.pgpass|\.git-credentials|credentials\.json)$",
    # ``.`` also across a newline: a file ``.env.<newline>…`` must not escape the overlay just because of its name.
    re.S,
)

#: The committed templates of an ``.env`` — they list the names a deployment needs, never the values (the cockpit reads
#: its markers, DEV-72). Poradca still overlays them (its own, stricter choice); NS Code shows them (DEV-86).
TEMPLATE_RE = re.compile(r"^\.env\.(?:example|sample|template)$")


def is_secret_name(name: str) -> bool:
    """Whether a file of this name carries secrets (the template of an ``.env`` included)."""
    return bool(SECRET_FILE_RE.match(name))


def is_template(name: str) -> bool:
    """Whether the file is the committed template of an ``.env`` — names without values."""
    return bool(TEMPLATE_RE.match(name))
