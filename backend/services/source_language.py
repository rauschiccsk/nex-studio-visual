"""The source code a customer receives is all English (DEV-65).

Director 11.10.2026: „Všetko čo bude zobrazené v kokpite zostáva po slovensky. Ak napríklad spravíme aplikáciu
zákazníkovi pre zákazku, tak keď zákazníkovi odovzdávame zdrojové kódy, tam má byť všetko po anglicky.“

The agent is told so in every coding brief; this module is the check that does not rest on the agent's judgement.
It reads the lines a version ADDED (``base..head``, the same range the delivery statement counts) and reports Slovak
text in the parts of source code a person never sees on a screen: comments, docstrings, and — in test files — the
messages of assertions and the names of tests. String literals are left alone: they are the application's own text
for its users, in the customer's language. Text in double or Slovak quotation marks inside a comment is left alone
too — it quotes a screen text or a person.

Slovak written without diacritics is known by its words (DEV-183): ``slovak_words.txt`` beside this module lists Slovak
words as they are written without diacritics, none of them an English word. What the rule cannot see (Slovak names in
the code itself, other file types) the Auditor reviews by its charter.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from backend.services import delivered_tokens

#: Letters only Slovak (of the languages we meet in code) writes — the mechanical sign of a Slovak sentence.
SLOVAK_LETTERS = re.compile(r"[áäčďéíľĺňóôŕšťúýž]", re.IGNORECASE)
#: Quoted text inside a comment or a message — a screen text or a person's words, kept as they are. A Slovak quotation
#: opens with „ and closes with “ or ” — or with a straight ", as many sources write it (DEV-80). The proper closing
#: mark is tried first, so straight quotes inside a quotation stay in it; neither form reaches past the next „, so two
#: quotations on a line never swallow the words between them.
_QUOTED = re.compile(r"„[^„“”\n]*[“”]|„[^„“”\"\n]*\"|\"[^\"\n]*\"|`[^`\n]*`")
#: DEV-183: Slovak words as they are written without diacritics — a Slovak sentence the letters do not give away
#: („Ponuky“, ``test_override_aplikacie_422``). One per line; ``#`` starts a note.
WORDS_FILE = Path(__file__).with_name("slovak_words.txt")
SLOVAK_WORDS: frozenset[str] = frozenset(
    line.strip()
    for line in WORDS_FILE.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
)
_WORD = re.compile(r"[A-Za-z]+")
#: The words of one name: ``nezaradenaPosta`` → ``nezaradena``, ``Posta``; ``HTTPServer`` → ``HTTP``, ``Server``.
_NAME_PART = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
#: A path or an address — the application's own names (``/aktualizacie``, ``web/src/pages/profile``), not a sentence.
_PATH = re.compile(r"\S*/\S*")

PYTHON = (".py",)
SCRIPT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
#: Files whose comments start with ``#`` and run to the end of the line.
_HASH_FILES = re.compile(r"(\.(sh|bash|ya?ml|toml|ini|conf|cfg|env\.example)$|(^|/)(Dockerfile[^/]*|Makefile)$)")

WHAT_COMMENT = "komentár"
WHAT_DOCSTRING = "docstring"
WHAT_TEST_MESSAGE = "hláška skúšky"
WHAT_TEST_NAME = "meno skúšky"

_TEST_NAME = re.compile(
    r"\b(?:it|test|describe)(?:\.(?:each|only|skip)(?:\([^)]*\))?)?\(\s*([\"'`])((?:\\.|(?!\1).)*)\1"
)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class SlovakText:
    """One place in the added code with Slovak text that is not the application's text."""

    path: str
    line: int
    what: str
    text: str

    def location(self) -> str:
        return f"{self.path}:{self.line}"


class GitUnreadable(RuntimeError):
    """The repository could not be read — the check cannot say anything about the code."""


def _git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitUnreadable(str(exc)) from exc
    if done.returncode != 0:
        raise GitUnreadable(done.stderr.decode("utf-8", "replace").strip()[:300])
    return done.stdout.decode("utf-8", "replace")


def added_line_numbers(repo: Path, base: str, head: str) -> dict[str, set[int]]:
    """Line numbers (in ``head``) of the lines added or changed between ``base`` and ``head``, per file."""
    # core.quotepath=off: a path with non-ASCII letters arrives as written, not as an escaped, quoted string.
    diff = _git(
        repo,
        "-c",
        "core.quotepath=off",
        "diff",
        "-M",
        "-C",
        "--find-copies-harder",
        "-U0",
        "--no-color",
        f"{base}..{head}",
    )
    added: dict[str, set[int]] = {}
    path: Optional[str] = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif path is not None and (m := _HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or "1")
            added.setdefault(path, set()).update(range(start, start + count))
    return added


def slovak_words(text: str, own: frozenset[str] = frozenset()) -> list[str]:
    """The words of ``text`` that are Slovak written without diacritics — outside quoted parts, paths and the
    project's own name (``own``, lower case: Career Asistent is the application's name, not a Slovak sentence)."""
    found = []
    for word in _WORD.findall(_PATH.sub(" ", _QUOTED.sub("", text))):
        for part in _NAME_PART.findall(word):
            if part.lower() in SLOVAK_WORDS and part.lower() not in own:
                found.append(part.lower())
    return found


def is_slovak(text: str, own: frozenset[str] = frozenset()) -> bool:
    """Slovak letters outside quoted parts, or a Slovak word written without them (DEV-183)."""
    return bool(SLOVAK_LETTERS.search(_QUOTED.sub("", text))) or bool(slovak_words(text, own))


def own_name(repo: Path) -> frozenset[str]:
    """The words of the project's own name — its repository's directory (``career-asistent``)."""
    return frozenset(part for part in re.split(r"[^a-z]+", repo.name.lower()) if part)


def _python_parts(source: str, is_test: bool) -> Iterator[tuple[int, str, str]]:
    """``(line, what, text)`` for every comment, docstring line and — in a test — test name and assertion message
    line."""
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], WHAT_COMMENT, tok.string.lstrip("#")
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if is_test and (
            (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"))
            or (isinstance(node, ast.ClassDef) and node.name.startswith("Test"))
        ):
            # DEV-183: a test's name is read as its words — ``test_override_aplikacie_422`` names a Slovak test.
            yield node.lineno, WHAT_TEST_NAME, node.name
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                # The docstring's own text, line by line — its triple quotes are not a quotation of anything.
                for offset, text in enumerate(first.value.value.split("\n")):
                    yield first.lineno + offset, WHAT_DOCSTRING, text
        if not is_test:
            continue
        message: Optional[ast.AST] = None
        if isinstance(node, ast.Assert):
            message = node.msg
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            name = getattr(node.exc.func, "id", None) or getattr(node.exc.func, "attr", None)
            if name == "AssertionError":
                message = node.exc.args[0]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "fail" and node.args:
            message = node.args[0]  # pytest.fail("…")
        if message is not None:
            for sub in ast.walk(message):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    yield sub.lineno, WHAT_TEST_MESSAGE, sub.value


def _script_parts(source: str, is_test: bool) -> Iterator[tuple[int, str, str]]:
    """``(line, what, text)`` for every ``//`` and ``/* */`` comment line and — in a test — every test name."""
    line, i, n = 1, 0, len(source)
    quote: Optional[str] = None
    while i < n:
        c = source[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                line += 1
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            yield line, WHAT_COMMENT, source[i + 2 : end]
            i = end
            continue
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            for offset, text in enumerate(source[i:end].split("\n")):
                yield line + offset, WHAT_COMMENT, text
            line += source.count("\n", i, end)
            i = end
            continue
        elif c == "\n":
            line += 1
        i += 1
    if is_test:
        for number, text in enumerate(source.splitlines(), 1):
            for m in _TEST_NAME.finditer(text):
                yield number, WHAT_TEST_NAME, m.group(2)


def _hash_comment(text: str) -> Optional[str]:
    """The comment of a ``#`` line — from the first ``#`` outside quotes to the end. Quotes are followed only up to it:
    DEV-182, the line „… on the project's own runner. … a Štandardný project's“ lost its middle to a pair of
    apostrophes taken for a quoted string, and the Slovak word in it passed unread."""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return text[i + 1 :]
    return None


def _hash_parts(source: str) -> Iterator[tuple[int, str, str]]:
    """``(line, what, text)`` for ``#`` comments outside quotes (shell, YAML, Dockerfile, …)."""
    for number, text in enumerate(source.splitlines(), 1):
        comment = _hash_comment(text)
        if comment is not None:
            yield number, WHAT_COMMENT, comment


def _parts_of(path: str, source: str, is_test: bool) -> Optional[Iterator[tuple[int, str, str]]]:
    """The comments, docstrings, test messages and test names of one file by its kind — ``None`` for a kind this rule
    does not read."""
    if path.endswith(PYTHON):
        return _python_parts(source, is_test)
    if path.endswith(SCRIPT):
        return _script_parts(source, is_test)
    if _HASH_FILES.search(path):
        return _hash_parts(source)
    return None


def slovak_in(path: str, source: str, *, is_test: bool = False, own: frozenset[str] = frozenset()) -> list[SlovakText]:
    """Slovak text in every line of one file — what :func:`scan` reports for the lines a version added. DEV-182: the
    cockpit's templates that become a project's code are held to it before any project gets them. ``own`` is the
    project's own name (:func:`own_name`)."""
    parts = _parts_of(path, source, is_test)
    found: dict[int, SlovakText] = {}
    for number, what, text in parts or ():
        if number not in found and is_slovak(text, own):
            found[number] = SlovakText(path=path, line=number, what=what, text=text.strip()[:160])
    return [found[n] for n in sorted(found)]


def scan(repo: Path, base: str, head: str, version: str = "") -> list[SlovakText]:
    """Slovak text in the comments, docstrings, test messages and test names a version added — in record order."""
    found: list[SlovakText] = []
    own = own_name(repo)
    for path, numbers in sorted(added_line_numbers(repo, base, head).items()):
        if not (path.endswith(PYTHON) or path.endswith(SCRIPT) or _HASH_FILES.search(path)):
            continue
        try:
            source = _git(repo, "show", f"{head}:{path}")
        except GitUnreadable:
            continue  # deleted or not text at head
        kind, _excluded = delivered_tokens.classify(path, head=source[:2000], version=version)
        if kind not in (delivered_tokens.KIND_CODE, delivered_tokens.KIND_TESTS):
            continue  # documentation, generated, the agent's notes … — not what this rule is about
        is_test = kind == delivered_tokens.KIND_TESTS
        seen: set[int] = set()
        for number, what, text in _parts_of(path, source, is_test) or ():
            if number in numbers and number not in seen and is_slovak(text, own):
                seen.add(number)
                found.append(SlovakText(path=path, line=number, what=what, text=text.strip()[:160]))
    found.sort(key=lambda f: (f.path, f.line))
    return found
