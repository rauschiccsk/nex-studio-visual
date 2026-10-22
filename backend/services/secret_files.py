"""Which files of a project carry secrets — one rule for every reader that shows a project's files to somebody.

Poradca overlays them with an empty file in its container (:func:`backend.services.poradca.sandbox.overlay_paths`);
NS Code (DEV-86) never lists nor serves them. Kept in one place so the two can never disagree about what a secret
is — a second copy of the pattern would be the one somebody forgets to extend.
"""

from __future__ import annotations

import re

from backend.services.env_contract import VARIABLE

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
#: its markers, DEV-72). Poradca sees them through :func:`names_only` (DEV-177); NS Code shows them (DEV-86).
TEMPLATE_RE = re.compile(r"^\.env\.(?:example|sample|template)$")


def is_secret_name(name: str) -> bool:
    """Whether a file of this name carries secrets (the template of an ``.env`` included)."""
    return bool(SECRET_FILE_RE.match(name))


def is_template(name: str) -> bool:
    """Whether the file is the committed template of an ``.env`` — names without values."""
    return bool(TEMPLATE_RE.match(name))


def names_only(text: str) -> str:
    """A template with its comments, markers and blank lines, and of each variable only ``NAME=`` — no value, even one
    written into the template by mistake (DEV-177: what Poradca reads). It keeps exactly what the cockpit reads the
    markers from (:func:`backend.services.env_contract.parse`): a comment block and the variable below it.

    A quoted value over several lines is cut whole — a line inside it is not a comment, whatever it starts with. Any
    other line (``export NAME=…``, a broken one) is left out whole: a line that is not a variable is not a name."""
    kept: list[str] = []
    open_quote: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if open_quote:
            if line.endswith(open_quote):
                open_quote = None
            continue
        if not line or line.startswith("#"):
            kept.append(raw)
            continue
        variable = VARIABLE.match(line)
        if variable is None:
            continue
        value = line[variable.end() :]
        if value[:1] in ("'", '"') and not (len(value) > 1 and value.endswith(value[0])):
            open_quote = value[0]
        kept.append(f"{variable.group(1)}=")
    return "".join(f"{line}\n" for line in kept)
