"""DEV-50 — the delivered-token statement of a version: what we delivered, counted as tokens, as the basis of invoicing.

Director 10.10.2026, decided one point at a time (the ticket quotes each answer):

* the basis is the code we DELIVERED — the difference in the customer's repository between two delivered versions —
  not the tokens the agent spent ("Zákazník nemá platiť za naše neúspešné pokusy, ale za skutočne dodaný produkt");
* only added and changed lines count, in their new form; deleted lines and moved or renamed files do not;
* three kinds — code, tests, documentation; documentation is its own line with its own (lower) rate;
* documentation is what the customer receives — specification, design, task plan, release notes, the living spec
  documents — never the spec copies the cockpit files under each version, any version's Zadanie, the agent's memory;
* a fix of our own error (the code does not do what the approved specification says) is never billed;
* tokens are counted with the public standard o200k_base, shipped inside the cockpit and pinned — no network, no
  outside service; a customer can recount the same number with the same standard.

This module is the counting: which files count as what (:func:`classify` — one rule, read by every caller), the
added lines between two commits, and the tokens. Pricing, the work kind and issuing live with their callers.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import tiktoken
import tiktoken_ext.openai_public as _openai_public

#: The tokenizer — a public, pinned standard (Director: „Áno, súhlasím s o200k_base v kokpite“).
TOKENIZER = "o200k_base"
#: Its vocabulary, shipped with the cockpit — never downloaded. The hash it must have is the one the pinned tiktoken
#: release itself declares for o200k_base (read from the library at load), so a changed file cannot count quietly.
VOCABULARY = Path(__file__).resolve().parents[1] / "resources" / "o200k_base.tiktoken"

KIND_CODE = "kod"
KIND_TESTS = "skusky"
KIND_DOCS = "dokumentacia"
KINDS = (KIND_CODE, KIND_TESTS, KIND_DOCS)

#: Why a file is left out — the sentence the statement shows next to it.
EXCLUDED_LOCKFILE = "zámok závislostí — vytvára ho nástroj, nie my"
EXCLUDED_GENERATED = "vygenerovaný súbor — vytvára ho nástroj, nie my"
EXCLUDED_BINARY = "obrázok alebo dáta — nie je to text, ktorý sme napísali"
EXCLUDED_SPEC_COPY = "zrkadlo špecifikácie odložené k tejto verzii — tie isté zmeny sa rátajú v živých dokumentoch"
EXCLUDED_ZADANIE = "Zadanie — napísal ho zákazník"
EXCLUDED_AGENT_NOTES = "pracovné poznámky a pravidlá agenta — nie sú súčasťou dodávky"

_LOCKFILES = re.compile(r"(^|/)(package-lock\.json|poetry\.lock|yarn\.lock|pnpm-lock\.yaml|uv\.lock|[^/]+\.lock)$")
_GENERATED_PATH = re.compile(r"(\.generated\.|\.min\.(js|css)$|(^|/)(dist|build)/)")
_GENERATED_HEADER = re.compile(r"auto-?generated|@generated|do not edit", re.IGNORECASE)
_BINARY = re.compile(
    r"\.(png|jpe?g|gif|webp|ico|svg|bmp|tiff?|pdf|zip|gz|tar|7z|woff2?|ttf|otf|eot|mp[34]|wav|webm|mov|"
    r"xlsx?|docx?|pptx?|sqlite3?|db|bin|exe|dll|so|tiktoken)$",
    re.IGNORECASE,
)
_ZADANIE = re.compile(r"(^|/)customer-requirements\.md$")
_AGENT_NOTES = re.compile(r"(^|/)(MEMORY\.md|CLAUDE\.md)$|^\.claude/|^docs/session-logs/|\.log$")
_TESTS = re.compile(r"(^|/)(tests?|__tests__|e2e)/|\.(test|spec)\.[a-z]+$|(^|/)test_[^/]*\.py$|(^|/)conftest\.py$")
_DOCS = re.compile(r"^docs/|\.(md|mdx|rst)$", re.IGNORECASE)


def _version_mirror(version: str) -> Optional[re.Pattern[str]]:
    """The version's own copy of the specification: ``docs/specs/versions/v<version>/spec/``.

    A project that keeps its living documents in ``docs/specs/…`` mirrors them into that folder for each version
    (NEX Inbox) — the same edits twice. Only THIS version's mirror is left out: another project keeps its living
    specification in an older version's folder (NEX Manager edits ``versions/v0.1.0/spec/`` in every version), and
    those edits are real documentation."""
    number = version.strip().lstrip("vV")
    return re.compile(rf"^docs/specs/versions/v?{re.escape(number)}/spec/") if number else None


def classify(path: str, head: str = "", version: str = "") -> tuple[Optional[str], Optional[str]]:
    """``(kind, None)`` for a delivered file, ``(None, reason)`` for one that is left out — THE rule.

    ``head`` is the beginning of the file's new content: a file that announces itself as generated is generated
    wherever it lives. ``version`` is the version being counted (its own spec mirror is left out). Order matters —
    what is excluded is decided before what kind of delivered text it is."""
    mirror = _version_mirror(version)
    if _LOCKFILES.search(path):
        return None, EXCLUDED_LOCKFILE
    if _BINARY.search(path):
        return None, EXCLUDED_BINARY
    if _GENERATED_PATH.search(path) or _GENERATED_HEADER.search(head):
        return None, EXCLUDED_GENERATED
    if mirror is not None and mirror.search(path):
        return None, EXCLUDED_SPEC_COPY
    if _ZADANIE.search(path):
        return None, EXCLUDED_ZADANIE
    if _AGENT_NOTES.search(path):
        return None, EXCLUDED_AGENT_NOTES
    if _TESTS.search(path):
        return KIND_TESTS, None
    if _DOCS.search(path):
        return KIND_DOCS, None
    return KIND_CODE, None


class VocabularyMismatch(RuntimeError):
    """The shipped vocabulary is not the one the pinned tokenizer declares — counting would not be o200k_base."""


_lock = threading.Lock()


def _ranks(expected_hash: Optional[str]) -> dict[bytes, int]:
    import base64

    data = VOCABULARY.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if expected_hash is None or actual != expected_hash:
        raise VocabularyMismatch(
            f"slovník tokenizéra {VOCABULARY} má odtlačok {actual}, štandard {TOKENIZER} žiada {expected_hash}"
        )
    ranks = {}
    for line in data.splitlines():
        if line:
            token, rank = line.split()
            ranks[base64.b64decode(token)] = int(rank)
    return ranks


@lru_cache(maxsize=1)
def encoding() -> tiktoken.Encoding:
    """o200k_base, built from the shipped vocabulary — exactly the library's own definition (its pattern, its special
    tokens, its expected hash), only the file comes from the cockpit instead of the internet."""
    with _lock:
        download = _openai_public.load_tiktoken_bpe
        _openai_public.load_tiktoken_bpe = lambda _url, expected_hash=None: _ranks(expected_hash)
        try:
            params = _openai_public.o200k_base()
        finally:
            _openai_public.load_tiktoken_bpe = download
    return tiktoken.Encoding(**params)


def tokenizer_label() -> str:
    """What the statement names as its measure — the standard and the library version it is pinned to."""
    return f"{TOKENIZER} (tiktoken {tiktoken.__version__})"


def count_tokens(text: str) -> int:
    """Tokens of ``text``; text that happens to look like a special token is counted as plain text."""
    return len(encoding().encode(text, disallowed_special=()))


# ── the added lines between two delivered commits ───────────────────────────


@dataclass
class FileRow:
    path: str
    lines: int = 0
    tokens: int = 0
    kind: Optional[str] = None
    excluded: Optional[str] = None


@dataclass
class Count:
    """What one version delivered between ``base`` and ``head``."""

    base: str
    head: str
    files: list[FileRow] = field(default_factory=list)

    def tokens(self, kind: str) -> int:
        return sum(f.tokens for f in self.files if f.kind == kind)

    def lines(self, kind: str) -> int:
        return sum(f.lines for f in self.files if f.kind == kind)


class GitUnreadable(RuntimeError):
    """git could not answer about the project — the statement cannot be counted."""


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitUnreadable(f"git sa nedá spustiť: {exc}") from exc
    if done.returncode != 0:
        raise GitUnreadable(done.stderr.decode("utf-8", "replace").strip()[:300] or "git skončil chybou")
    return done.stdout.decode("utf-8", "replace")


def added_lines(repo: Path, base: str, head: str) -> tuple[dict[str, list[str]], set[str]]:
    """Added and changed lines (their new form) per file between ``base`` and ``head``, and the binary files.

    Moved, renamed and copied files count only for what changed in them (``-M -C --find-copies-harder``); deleted
    lines never appear (Director: „len pridané a zmenené riadky“)."""
    diff = _git(repo, "diff", "-M", "-C", "--find-copies-harder", "-U0", "--no-color", f"{base}..{head}")
    added: dict[str, list[str]] = {}
    binary: set[str] = set()
    path: Optional[str] = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path is not None:
                added.setdefault(path, [])
        elif line.startswith("Binary files ") and " and b/" in line:
            binary.add(line.rsplit(" and b/", 1)[1].removesuffix(" differ"))
        elif path is not None and line.startswith("+"):
            added[path].append(line[1:])
    return added, binary


def count(repo: Path, base: str, head: str, version: str = "") -> Count:
    """Classify and count every file the version delivered (excluded ones listed with their reason)."""
    added, binary = added_lines(repo, base, head)
    result = Count(base=base, head=head)
    for path in sorted(set(added) | binary):
        lines = added.get(path, [])
        if path in binary:
            result.files.append(FileRow(path=path, excluded=EXCLUDED_BINARY))
            continue
        kind, excluded = classify(path, "\n".join(lines[:5]), version)
        text = "\n".join(lines)
        result.files.append(
            FileRow(path=path, lines=len(lines), tokens=count_tokens(text) if kind else 0, kind=kind, excluded=excluded)
        )
    return result
