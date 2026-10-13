"""Where a generated app decides "is this the live preview?" outside the template's switch (DEV-43).

The cockpit starts the Vizuál preview with ``VITE_PREVIEW`` set (:mod:`backend.services.vizual_sandbox`). In the
app only the template's helper ``isPreviewEnabled()`` (``frontend/src/preview/isPreview.ts``) may decide: it
compares with the list of enabling values. The raw flag used as a truth value lets ``VITE_PREVIEW=false``
through, so an app built with the preview "off" would act as the preview — stop sending an expired session to
the login, let the route guard in, or serve the fixtures instead of the backend. Measured 09.10.2026: Career
Asistent's API client, NEX Websites' route guard and its MSW start. The agent charter itself dictated the first
shape (fixed in DEV-2) and nothing in the cockpit read the app's code.

The build config decides too (DEV-1): it is the one place outside ``frontend/src`` that shapes what a release
contains. Measured 13.10.2026: NEX Manager's ``frontend/vite.config.ts`` read ``process.env.VITE_PREVIEW`` as a truth
value in three places, so a build made with ``VITE_PREVIEW=false`` would keep the preview's MSW worker and release
notes — and this rule, reading only ``frontend/src``, never saw it. The flag is therefore any ``env`` object's
``VITE_PREVIEW`` — ``import.meta.env``, ``process.env`` or a ``loadEnv`` result.

The rule is about the SHAPE of every use of the raw flag in the app's frontend source, not a list of files.
A use is allowed only as:

* the static guard ``import.meta.env.VITE_PREVIEW && isPreviewEnabled(…)`` — Vite turns the flag into a literal,
  so without the variable the preview branch is dead code and never reaches the release bundle;
* an operand of a comparison (``=== "1"``, ``typeof … === "string"``) — a decision by value, not by truth;
* a value handed on — the first argument of ``isPreviewEnabled(…)`` or of a list's ``.includes(…)``, or the
  right-hand side of an assignment or a default parameter (``value = import.meta.env.VITE_PREVIEW``), optionally
  ``as <type>`` or ``?? <fallback>``.

Anything else — ``if (flag)``, ``!flag``, ``flag ? a : b``, ``flag || x``, ``flag && other()``, ``Boolean(flag)``,
a bare argument — is a finding. Comments are blanked first (line numbers kept) and tests are not the app. A value
handed to a name is not followed further: what decides is then the code that reads the name, and in every
project measured that is a comparison or the helper itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The app's frontend source, relative to the project root.
FRONTEND_SRC = Path("frontend") / "src"
#: The build config's name in ``frontend/`` (``vite.config.ts``, ``.mjs``, …). ``vitest.config.*`` is test tooling, and
#: ``vite.config.ts.timestamp-….mjs`` is Vite's bundled copy of the config, left behind when a dev server dies.
BUILD_CONFIG_STEM = "vite.config"

#: The template's decision helper every finding points to.
HELPER_REL = "frontend/src/preview/isPreview.ts"

SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs"})
CONFIG_SUFFIXES = SOURCE_SUFFIXES | {".cts", ".cjs"}

#: Directories under ``frontend/src`` that hold tests, not the app.
_TEST_DIRS = frozenset({"__tests__", "test", "tests"})
_TEST_FILE = re.compile(r"\.(?:test|spec)\.[^.]+$|^setupTests\.[^.]+$")

_ENV = r"(?:import\.meta\.env|\bprocess\.env|\benv)"
_FLAG = re.compile(rf"""{_ENV}(?:\?\.|\.)VITE_PREVIEW\b|{_ENV}\[\s*(["'])VITE_PREVIEW\1\s*\]""")

#: Block comments, and line comments not preceded by ``:`` (so a URL in a string is not cut as a comment).
_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\\])//[^\n]*", re.DOTALL)

_GUARD_AFTER = re.compile(r"\s*&&\s*isPreviewEnabled\s*\(")
_COMPARE_AFTER = re.compile(r"\s*[!=]==?(?!=)")
_COMPARE_BEFORE = re.compile(r"[!=]==?\s*$")
_HELPER_ARG_BEFORE = re.compile(r"(?:\bisPreviewEnabled|\.includes)\s*\(\s*$")
_HELPER_ARG_AFTER = re.compile(r"\s*(?:\?\?[^,;)\n]+)?[,)]")
_ASSIGN_BEFORE = re.compile(r"(?<![=!<>+\-*/%&|^?])=\s*$")
_ASSIGN_AFTER = re.compile(r"\s*(?:as\s+[^;,)\n]+|\?\?[^;,)\n]+)?\s*(?:[;,)\]}]|\n|\Z)")


@dataclass(frozen=True)
class PreviewDecision:
    """One use of the raw flag the rule does not allow: where it is and the line as written."""

    path: str
    line: int
    code: str


def _is_test(rel: Path) -> bool:
    return any(part in _TEST_DIRS for part in rel.parts[:-1]) or bool(_TEST_FILE.search(rel.name))


def _blank_comments(text: str) -> str:
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _allowed(code: str, start: int, end: int) -> bool:
    before, after = code[max(0, start - 200) : start], code[end : end + 200]
    if _GUARD_AFTER.match(after):
        return True
    if _COMPARE_AFTER.match(after) or _COMPARE_BEFORE.search(before):
        return True
    if _HELPER_ARG_BEFORE.search(before) and _HELPER_ARG_AFTER.match(after):
        return True
    return bool(_ASSIGN_BEFORE.search(before) and _ASSIGN_AFTER.match(after))


def _app_files(root: Path) -> list[Path]:
    """The frontend source outside tests, and the build config."""
    src = root / FRONTEND_SRC
    files: list[Path] = []
    if src.is_dir():
        files += [
            p
            for p in src.rglob("*")
            if p.suffix in SOURCE_SUFFIXES and p.is_file() and not _is_test(p.relative_to(src))
        ]
    frontend = root / FRONTEND_SRC.parent
    if frontend.is_dir():
        files += [
            p
            for p in frontend.glob(f"{BUILD_CONFIG_STEM}.*")
            if p.stem == BUILD_CONFIG_STEM and p.suffix in CONFIG_SUFFIXES and p.is_file()
        ]
    return sorted(files)


def find(project_root: Path) -> list[PreviewDecision]:
    """Every use of ``VITE_PREVIEW`` in the app's frontend source and build config the rule does not allow, ordered
    by path.

    ``[]`` when there is neither — nothing there decides about a preview. A file that cannot be read is a finding
    itself: an unchecked file is not a checked one."""
    root = Path(project_root)
    found: list[PreviewDecision] = []
    for path in _app_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            found.append(PreviewDecision(rel, 0, f"súbor sa nedal prečítať ({exc.__class__.__name__})"))
            continue
        code = _blank_comments(text)
        lines = text.splitlines()
        for m in _FLAG.finditer(code):
            if _allowed(code, m.start(), m.end()):
                continue
            line = code.count("\n", 0, m.start()) + 1
            found.append(PreviewDecision(rel, line, lines[line - 1].strip()[:160]))
    return found


def finding_lines(found: list[PreviewDecision]) -> list[str]:
    """One line per finding for the agent: ``path:line — `code```."""
    return [f"{d.path}:{d.line} — `{d.code}`" for d in found]


def places(count: int) -> str:
    """``1 miesto`` · ``3 miesta`` · ``5 miest`` — the Slovak plural the Manažér reads."""
    if count == 1:
        return "1 miesto"
    return f"{count} miesta" if 2 <= count <= 4 else f"{count} miest"


def fix_instruction() -> str:
    """What the agent does at every finding — the fix brief of the floored Verifikácia FAIL."""
    return (
        "O živom náhľade smie rozhodovať len pomocník `isPreviewEnabled()` zo šablóny "
        f"(`{HELPER_REL}`; ak ho projekt nemá, vytvor ho v tvare šablóny). Na každom uvedenom mieste nahraď "
        "pravdivostné použitie `import.meta.env.VITE_PREVIEW` volaním `isPreviewEnabled()`. Premenná smie ostať "
        "len ako statická poistka v tvare `import.meta.env.VITE_PREVIEW && isPreviewEnabled()`, v porovnaní "
        "s hodnotou alebo ako hodnota odovzdaná pomocníkovi. V nastavení stavby (`frontend/vite.config.*`) "
        'odovzdaj hodnotu pomocníkovi výslovne: `isPreviewEnabled(process.env.VITE_PREVIEW ?? "")` — bez `?? ""` '
        "by pomocník čítal `import.meta.env`, ktoré tam nie je. Dôvod: aj `VITE_PREVIEW=false` je pravdivé, takže "
        "aplikácia zostavená s vypnutým náhľadom by sa správala ako náhľad."
    )
