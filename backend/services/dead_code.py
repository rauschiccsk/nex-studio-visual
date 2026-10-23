"""Code nobody uses does not reach a customer — the dead-code check of every version of every project (DEV-156).

Director 20.10.2026, after the cockpit's own deep check (DEV-155): „Chcem aby takáto kontrola bola zabudovaná do
kokpitu.“ To the plan: „Súhlasím“; 23.10.2026 to its size and its rules: „Áno, schvaľujem všetkých 10 bodov“.

What is judged — in the files the version's commit tracks, never in what a build left beside them:

* **Python** (vulture): functions, classes, methods, properties, imports and unreachable code nothing calls. Code only
  the tests call is not used — a customer pays for code that does something (DEV-50). What a framework or a library
  calls through a decorator or by name is left alone (:data:`CALLED_BY_DECORATOR`, :data:`CALLED_BY_NAME`), and so
  are modules that only re-export.
* **Frontend** (knip, in every package of the project): files nothing loads, exports and types nothing imports,
  dependencies nothing uses — the application's own code (``src/``); the tests do not count as a use.
* **Routes**: a route of the backend nothing calls — the FastAPI routes read with the Python parser, the calls with the
  TypeScript one in every package (as the cockpit's own guard reads its own) and with the Python one in the backend (a
  service that calls another over HTTP).

Only what a version ADDED stops it: the findings at its end minus those at its beginning — the delivery statement's
range, so the check runs before anything is counted for the customer. The old ones are offered as a cleanup the
Manažér starts himself (the project page). What looks unused but is called from outside the code — a scheduler,
another system, a library hook — the agent lists with its reason in :data:`EXCEPTIONS_FILE`; an entry without a reason
does not count. The Manažér reads the exceptions a version added in its verdict and the Auditor reviews them.

knip READS A PROJECT'S CONFIGURATION BY RUNNING IT (``vite.config.ts``): that is foreign code. The TypeScript half runs
in a container of its own, from this backend's image (never pulled), with no network, the project read-only, as a user
that owns nothing on the host, ceilings on memory, processes and time, no Docker of the server, and only the installed
packages that really lie inside the project (:func:`_run_script_half`). What it prints goes to the log, never into a
sentence a person reads. The Python half only parses — trees read as git stores them, links never followed. A check
that cannot run never stops a version — the Manažér reads why it did not run, as with the source-language check
(DEV-65).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import vulture
import yaml

from backend.services import build_sandbox, smoke_scratch

logger = logging.getLogger(__name__)

#: Called through a decorator, never by name — FastAPI routes under any name of their router, Pydantic validators and
#: serializers, SQLAlchemy listeners, pytest fixtures, task and command registrations.
CALLED_BY_DECORATOR = [
    "@router.*",
    "@app.*",
    "@*.get",
    "@*.post",
    "@*.put",
    "@*.patch",
    "@*.delete",
    "@*.head",
    "@*.options",
    "@*.websocket",
    "@*.api_route",
    "@*.validator",
    "@*field_validator",
    "@*model_validator",
    "@*field_serializer",
    "@*model_serializer",
    "@*computed_field",
    "@pytest.fixture",
    "@event.listens_for",
    "@*.listens_for",
    "@*.on_event",
    "@*.exception_handler",
    "@*.middleware",
    "@*.task",
    "@*.command",
    "@*.callback",
]

#: Called by name by a library or a framework — a test runner, a migration tool, and the methods a library calls on
#: the subclass it is given (an HTTP server's handler, Starlette's middleware, urllib's redirect handler, httpx's
#: transport, an SMTP server's handler, a logging handler).
CALLED_BY_NAME = [
    "test_*",
    "Test*",
    "upgrade",
    "downgrade",
    "lifespan",
    "do_*",
    "log_message",
    "dispatch",
    "redirect_request",
    "http_error_*",
    "handle_request",
    "handle_async_request",
    "handle_DATA",
    "handle_RCPT",
    "handle_EHLO",
    "emit",
]

#: What is judged. Variables and attributes are left out on purpose: model fields and settings read by a framework
#: look exactly like a variable nobody reads.
JUDGED = {"function", "class", "method", "property", "import", "unreachable_code"}

#: vulture's own scale: 60 % is the lowest it gives a function nobody calls.
MIN_CONFIDENCE = 60

#: Decorators that only wrap or describe a function and never register it anywhere. A function under ANY OTHER decorator
#: may be registered by it — a framework's, or the project's own (``@feature`` in NEX Inbox's release assertions) — and
#: is not judged: stopping a version on a guess costs more than one decorated function left over.
NOT_REGISTERING = {
    "staticmethod",
    "classmethod",
    "property",
    "cached_property",
    "functools.cached_property",
    "lru_cache",
    "functools.lru_cache",
    "cache",
    "functools.cache",
    "wraps",
    "functools.wraps",
    "abstractmethod",
    "abc.abstractmethod",
    "override",
    "typing.override",
    "overload",
    "typing.overload",
    "contextmanager",
    "contextlib.contextmanager",
    "asynccontextmanager",
    "contextlib.asynccontextmanager",
    "dataclass",
    "dataclasses.dataclass",
    "total_ordering",
    "functools.total_ordering",
    "final",
    "typing.final",
}

#: Where the project lists what looks unused but is called from outside its code — each entry with its reason.
EXCEPTIONS_FILE = "docs/dead-code-exceptions.yml"

#: Not production code: the tests, wherever a project keeps them.
_TEST_PATH = re.compile(r"(^|/)(tests?|testing|__tests__)/|(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$")

#: The TypeScript half inside this backend's image (``templates/`` is copied to ``/app/templates``; the Dockerfile
#: installs its pinned tools beside it).
SCRIPT_IN_IMAGE = "/app/templates/dead-code/scan.mjs"
#: Where a tree is shown to the TypeScript half's container.
_WORK = "/work"
#: How long the TypeScript half may take for both trees of a version (seconds), and what of it is kept for the
#: container to start and to answer.
SCRIPT_CAP = 300
SCRIPT_MARGIN = 30
#: Ceilings of the TypeScript half's container.
_CONTAINER_LIMITS = [
    # Only this host's own image: a missing tag must never be looked up on a public registry, where anyone could have
    # published an image under the same name.
    "--pull",
    "never",
    "--network",
    "none",
    "--read-only",
    "--tmpfs",
    "/tmp:size=512m",
    "--memory",
    "2g",
    "--cpus",
    "2",
    "--pids-limit",
    "512",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    # A user that owns nothing on the host — not the uid of the people and services of the server.
    "--user",
    "65534:65534",
    "-e",
    "HOME=/tmp",
]

#: What of knip's report is judged, and how each finding is named.
_KNIP_KINDS = {"files": "file", "dependencies": "dependency", "exports": "export", "types": "type"}
_ROUTE_METHODS = ("get", "post", "put", "patch", "delete")
#: An address with a scheme — its path is what a route is matched against.
_ADDRESS = re.compile(r"^(https?|wss?|\{\})://")
_ANY = "*"

_SENTENCES = {
    "function": "funkciu {name} ({where}) nikto nevolá",
    "method": "metódu {name} ({where}) nikto nevolá",
    "class": "triedu {name} ({where}) nikto nepoužíva",
    "property": "vlastnosť {name} ({where}) nikto nečíta",
    "import": "import {name} ({where}) sa nepoužíva",
    "unreachable_code": "kód v {name} ({where}) sa nikdy nevykoná",
    "file": "súbor {where} nikto nenačíta",
    "export": "export {name} zo súboru {where} nikto nepoužíva",
    "type": "typ {name} zo súboru {where} nikto nepoužíva",
    "dependency": "balík {name} ({where}) nikto nepoužíva",
    "route": "trasu {name} ({where}) nevolá žiadna obrazovka",
}


@dataclass(frozen=True)
class Finding:
    """One piece of code nobody uses. ``path`` is relative to the repository; ``name`` is what the exceptions name."""

    kind: str
    path: str
    name: str
    line: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        """What a finding is compared by between the version's beginning and its end — never the line, which moves."""
        return (self.kind, self.path, self.name)

    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path

    def sentence(self) -> str:
        return _SENTENCES[self.kind].format(name=self.name, where=self.where())


@dataclass(frozen=True)
class Waiver:
    """An entry of :data:`EXCEPTIONS_FILE`: code called from outside the project's code, and why."""

    path: str
    name: str
    reason: str

    def covers(self, finding: Finding) -> bool:
        return finding.path == self.path and finding.name == self.name

    def sentence(self) -> str:
        return f"{self.name} ({self.path}) — {self.reason.rstrip('.')}"


@dataclass
class Report:
    """What the check found — split by whether the version added it."""

    #: Added by the version and not excepted — it stops the version.
    new: list[Finding] = field(default_factory=list)
    #: There before the version (or, without a beginning, everything) and not excepted — the cleanup.
    old: list[Finding] = field(default_factory=list)
    #: Exceptions the version added — the Manažér reads them, the Auditor reviews them.
    waivers_added: list[Waiver] = field(default_factory=list)
    #: Entries of the exceptions file that do not count, each as a sentence.
    invalid_waivers: list[str] = field(default_factory=list)
    #: Why a part of the check did not run, each as a sentence.
    not_run: list[str] = field(default_factory=list)


class CheckNotRun(Exception):
    """The check could not read what it judges — the reason is a sentence for the Manažér."""


# --- the trees -------------------------------------------------------------------------------------------------------


def _git(repo: Path, *args: str, timeout: int = 60) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise CheckNotRun(f"git {args[0]} skončil kódom {proc.returncode}")
    return proc.stdout


def _export(repo: Path, sha: str, dest: Path) -> Path:
    """The files the commit tracks, as git stores them — nothing a build left in the checkout (``dist/``, ``.venv``,
    ``node_modules``), no attribute of the tree applied (``export-ignore`` would hide a file from ``git archive``), and
    no symbolic link written: a link could point anywhere on this server, and the check reads what it finds."""
    dest.mkdir(parents=True)
    entries = []
    for record in _git(repo, "ls-tree", "-r", "-z", "--full-tree", sha).split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, kind, obj = meta.split(b" ")
        rel = Path(path.decode("utf-8", errors="surrogateescape"))
        if kind != b"blob" or mode not in (b"100644", b"100755") or rel.is_absolute() or ".." in rel.parts:
            continue
        entries.append((obj, rel))
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        for obj, rel in entries:
            proc.stdin.write(obj + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) != 3:
                raise CheckNotRun(f"zmena {sha[:8]} sa nedá prečítať z gitu ({rel})")
            body = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    finally:
        proc.stdin.close()
        proc.kill()
        proc.wait()
    return dest


def _renames(repo: Path, base: str, head: str) -> dict[str, str]:
    """What the version moved: ``{old path: new path}`` — a moved file is not new code."""
    tokens = _git(repo, "diff", "--name-status", "-M", "-z", base, head).decode("utf-8", errors="surrogateescape")
    parts = tokens.split("\0")
    moved: dict[str, str] = {}
    i = 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        if status[:1] in ("R", "C"):
            if status[:1] == "R":
                moved[parts[i + 1]] = parts[i + 2]
            i += 3
        else:
            i += 2
    return moved


def _python_files(tree: Path) -> list[Path]:
    return sorted(
        f
        for f in tree.rglob("*.py")
        if "node_modules" not in f.relative_to(tree).parts and not _TEST_PATH.search(f.relative_to(tree).as_posix())
    )


def _packages(tree: Path) -> list[str]:
    """The project's frontend packages — a ``package.json`` beside the application's own ``src/``."""
    return sorted(
        p.parent.relative_to(tree).as_posix()
        for p in tree.rglob("package.json")
        if "node_modules" not in p.relative_to(tree).parts and (p.parent / "src").is_dir()
    )


# --- Python ----------------------------------------------------------------------------------------------------------


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return None


def _only_re_exports(tree: ast.Module) -> bool:
    """A module of imports and ``__all__`` — vulture counts every name in ``__all__`` as used, so scanning it would keep
    anything it lists alive (DEV-155)."""
    defines = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in tree.body)
    declares_all = any(
        isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "__all__" for t in n.targets) for n in tree.body
    )
    return declares_all and not defines


@dataclass(frozen=True)
class _Def:
    first: int
    end: int
    qualname: str
    decorators: tuple[str, ...]


def _dotted(node: ast.expr) -> str:
    """``@a.b.c(…)`` → ``a.b.c``."""
    node = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _defs(tree: ast.Module) -> list[_Def]:
    """Every function and class of a module with its qualified name, its lines (decorators included) and decorators."""
    out: list[_Def] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                first = min([d.lineno for d in child.decorator_list] + [child.lineno])
                decorators = tuple(_dotted(d) for d in child.decorator_list)
                out.append(_Def(first, child.end_lineno or child.lineno, name, decorators))
                visit(child, f"{name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _registers(decorators: tuple[str, ...]) -> bool:
    return any(d not in NOT_REGISTERING and not d.endswith((".setter", ".getter", ".deleter")) for d in decorators)


def python_findings(root: Path, files: list[Path]) -> list[Finding]:
    """Every judged piece of Python code in ``files`` nothing in them calls. ``root`` makes the paths relative.

    A finding is named by its qualified name (``Invoice.total``) — two methods of one name in two classes are two
    findings; unreachable code by the function it is in."""
    parsed = {str(f): _parse(f) for f in files}
    re_exports = [name for name, t in parsed.items() if t is not None and _only_re_exports(t)]
    scan = vulture.Vulture(ignore_names=CALLED_BY_NAME, ignore_decorators=CALLED_BY_DECORATOR)
    scan.scavenge(list(parsed), exclude=re_exports)
    defs = {name: _defs(t) for name, t in parsed.items() if t is not None}
    found = []
    for item in scan.get_unused_code(min_confidence=MIN_CONFIDENCE):
        if item.typ not in JUDGED:
            continue
        line = item.first_lineno
        around = [d for d in defs.get(str(item.filename), []) if d.first <= line <= d.end]
        if item.typ == "unreachable_code":
            name = around[-1].qualname if around else "module"
        elif item.typ == "import":
            name = str(item.name)
        else:
            own = next((d for d in reversed(around) if d.qualname.rsplit(".", 1)[-1] == item.name), None)
            if own is not None and _registers(own.decorators):
                continue
            name = own.qualname if own is not None else str(item.name)
        found.append(Finding(item.typ, Path(item.filename).relative_to(root).as_posix(), name, line))
    return sorted(found, key=lambda f: (f.path, f.line, f.name))


def _path_of(text: str) -> str:
    """The path a literal names: an address loses its scheme and host (``http://{}/api/x`` → ``/api/x``), any its
    query."""
    if _ADDRESS.match(text):
        text = "/" + text.split("://", 1)[1].partition("/")[2]
    return text.split("?")[0]


def python_calls(files: list[Path]) -> list[dict[str, str]]:
    """The paths the backend itself names — a route one service calls on another over HTTP is called. The paths that
    DECLARE a route (a route decorator's path, a router's prefix) are not calls of it."""
    calls = []
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        declared: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                declared.update(id(d.args[0]) for d in node.decorator_list if isinstance(d, ast.Call) and d.args)
            if isinstance(node, ast.Call):
                declared.update(id(k.value) for k in node.keywords if k.arg == "prefix")
        for node in ast.walk(tree):
            if id(node) in declared:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            else:
                continue
            path = _path_of(text)
            if (path.startswith("/") or path.startswith("{}/")) and any(s and s != "{}" for s in path.split("/")):
                calls.append({"method": _ANY, "path": path})
    return calls


# --- routes ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    file: str
    line: int


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """``name = APIRouter(prefix="/x")`` → ``{"name": "/x"}``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        callee = node.value.func
        if (callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")) != "APIRouter":
            continue
        prefix = next(
            (
                k.value.value
                for k in node.value.keywords
                if k.arg == "prefix" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)
            ),
            "",
        )
        out.update({t.id: prefix for t in node.targets if isinstance(t, ast.Name)})
    return out


def routes(root: Path, files: list[Path]) -> list[Route]:
    """The FastAPI routes the files declare: ``@<router>.get("/path")`` & co., with the router's own prefix. A prefix
    given where the router is included (``/api/v1``) is not known here — :func:`reaches` matches the end of a path."""
    out = []
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        prefixes = _router_prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                    continue
                owner = deco.func.value
                if not isinstance(owner, ast.Name) or deco.func.attr not in _ROUTE_METHODS:
                    continue
                if owner.id not in prefixes and owner.id != "app" and not owner.id.endswith("router"):
                    continue
                if not (deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str)):
                    continue
                path = prefixes.get(owner.id, "") + deco.args[0].value
                out.append(Route(deco.func.attr.upper(), path, f.relative_to(root).as_posix(), deco.lineno))
    return out


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _call_segments(path: str) -> list[str]:
    """A call's segments; what is glued to the end of its last one (``/reports${qs}``) is not part of the path."""
    got = _segments(path)
    if got and got[-1] != "{}" and got[-1].endswith("{}"):
        got[-1] = got[-1].removesuffix("{}")
    return got


def _same(want: list[str], got: list[str]) -> bool:
    return all(w == g or (g == "{}" and w.startswith("{")) for w, g in zip(want, got, strict=True))


def reaches(route: str, call: str) -> bool:
    """Whether a path the frontend names (``{}`` for what it fills in) reaches a route (``{name}`` for a parameter).

    The call carries the base the router was included under (``/api/v1/invoices/{}``), the route may not
    (``/invoices/{invoice_id}``), so the route is matched against the END of the call. A call that starts with ``{}``
    is a helper's tail (``${base}/files``) and matches the end of the route."""
    want, got = _segments(route), _call_segments(call)
    if call.startswith("{}"):
        got = got[1:]
        return 0 < len(got) <= len(want) and _same(want[len(want) - len(got) :], got)
    return 0 < len(want) <= len(got) and _same(want, got[len(got) - len(want) :])


def orphan_routes(found: list[Route], calls: list[dict[str, str]]) -> list[Finding]:
    """Routes no call reaches. A route with no fixed segment (``/{id}`` of a router without a prefix) is not judged —
    any call would look like it."""
    out = []
    for r in found:
        if not any(not s.startswith("{") for s in _segments(r.path)):
            continue
        if any((c["method"] in (_ANY, r.method)) and reaches(r.path, c["path"]) for c in calls):
            continue
        out.append(Finding("route", r.file, f"{r.method} {r.path}", r.line))
    return out


# --- the TypeScript half ---------------------------------------------------------------------------------------------


def _installed(repo: Path, pkg: str) -> Optional[Path]:
    """The package's installed ``node_modules`` in the checkout — only when it really lies inside the project. The
    checkout is the agent's to write: a ``node_modules`` that is a link to elsewhere on this server is never shown to
    the container (a bind mount follows the link)."""
    candidate = repo / pkg / "node_modules"
    if not candidate.is_dir():
        return None
    real, project = Path(os.path.realpath(candidate)), Path(os.path.realpath(repo))
    if candidate.is_symlink() or not real.is_relative_to(project) or real == project:
        return None
    return real


def _run_script_half(repo: Path, trees: dict[str, Path], packages: list[str]) -> dict[str, Any]:
    """Run ``scan.mjs`` over the trees in its sealed container; its JSON report. The project's installed packages
    (``node_modules`` of the checkout — a tree from git has none) are shown to it read-only inside each tree. What the
    container says on its error stream comes from the project's code: it goes to the log, never into a sentence.

    Raises:
        CheckNotRun: the container could not run or its report is unreadable.
    """
    name = f"nsv-dead-code-{uuid.uuid4().hex[:12]}"
    argv = ["docker", "run", "--rm", "--name", name, *_CONTAINER_LIMITS, "--entrypoint", "node"]
    for tree_name, tree in trees.items():
        argv += ["-v", f"{tree}:{_WORK}/{tree_name}:ro"]
        for pkg in packages:
            modules = _installed(repo, pkg)
            if modules is not None and (tree / pkg).is_dir():
                (tree / pkg / "node_modules").mkdir(exist_ok=True)
                argv += ["-v", f"{modules}:{_WORK}/{tree_name}/{pkg}/node_modules:ro"]
    # The time is shared among the runs of knip, so that one slow package cannot take the others' report with it.
    job = {"trees": list(trees), "packages": packages, "budget_ms": (SCRIPT_CAP - SCRIPT_MARGIN) * 1000}
    argv += [build_sandbox.sandbox_image(), SCRIPT_IN_IMAGE, json.dumps(job)]
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=SCRIPT_CAP, check=False)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=30, check=False)
        raise CheckNotRun(f"kontrola frontendu nedobehla do {SCRIPT_CAP} s") from exc
    except OSError as exc:
        raise CheckNotRun("kontajner kontroly frontendu sa nedal spustiť") from exc
    if proc.stderr.strip():
        logger.warning("dead code container %s (%.0f s): %s", name, time.monotonic() - started, proc.stderr[-2000:])
    if proc.returncode != 0:
        raise CheckNotRun(f"kontajner kontroly frontendu skončil kódom {proc.returncode}")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CheckNotRun("kontrola frontendu nevrátila čitateľný výsledok") from exc
    if not isinstance(report, dict):
        raise CheckNotRun("kontrola frontendu nevrátila čitateľný výsledok")
    return report


def knip_findings(pkg: str, issues: list[dict[str, Any]]) -> list[Finding]:
    """knip's report of one package as findings — paths relative to the repository."""
    base = "" if pkg in ("", ".") else f"{pkg}/"
    out = []
    for issue in issues:
        file = f"{base}{issue.get('file', '')}"
        for key, kind in _KNIP_KINDS.items():
            for item in issue.get(key) or []:
                name = file if kind == "file" else str(item.get("name", ""))
                out.append(Finding(kind, file, name, int(item.get("line") or 0)))
    return out


# --- the exceptions --------------------------------------------------------------------------------------------------


def waivers(tree: Path) -> tuple[list[Waiver], list[str]]:
    """The exceptions file of a tree: the valid entries and a sentence for every entry that does not count."""
    path = tree / EXCEPTIONS_FILE
    if not path.is_file():
        return [], []
    try:
        entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return [], [f"{EXCEPTIONS_FILE} sa nedá prečítať ({str(exc).splitlines()[0]}) — žiadna výnimka neplatí"]
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        return [], [f"{EXCEPTIONS_FILE} nie je zoznam výnimiek — žiadna výnimka neplatí"]
    valid, invalid = [], []
    for number, entry in enumerate(entries, start=1):
        fields = entry if isinstance(entry, dict) else {}
        path_, name, reason = (str(fields.get(k) or "").strip() for k in ("path", "name", "reason"))
        if not (path_ and name):
            invalid.append(f"výnimka č. {number} v {EXCEPTIONS_FILE} nemenuje súbor (path) a meno (name) — neplatí")
        elif not reason:
            invalid.append(f"výnimka {name} ({path_}) v {EXCEPTIONS_FILE} nemá dôvod (reason) — neplatí")
        else:
            valid.append(Waiver(path_, name, reason))
    return valid, invalid


# --- the check -------------------------------------------------------------------------------------------------------


@dataclass
class _Tree:
    """What one tree holds: its findings, and per frontend package the reason it could not be checked."""

    findings: list[Finding] = field(default_factory=list)
    failed_packages: dict[str, str] = field(default_factory=dict)
    #: Whether its routes were judged — only where a frontend could call them.
    routes_judged: bool = False


def _read_tree(tree: Path, script: Optional[dict[str, Any]]) -> _Tree:
    files = _python_files(tree)
    out = _Tree(findings=python_findings(tree, files))
    if script is None:
        return out
    calls: list[dict[str, str]] = []
    for pkg, result in script.items():
        if result.get("error"):
            out.failed_packages[pkg] = str(result["error"])
        else:
            out.findings += knip_findings(pkg, result.get("issues") or [])
        calls += result.get("calls") or []
    if script:
        out.findings += orphan_routes(routes(tree, files), calls + python_calls(files))
        out.routes_judged = True
    return out


def _place(f: Finding) -> tuple[str, int, str]:
    return (f.path, f.line, f.name)


def _moved(f: Finding, renames: dict[str, str]) -> Finding:
    """A finding of the beginning where the version moved its file — compared with the end under its new path."""
    path = renames.get(f.path, f.path)
    return Finding(f.kind, path, path if f.kind == "file" else f.name, f.line)


def _judge(
    head: _Tree,
    base: Optional[_Tree],
    head_waivers: list[Waiver],
    base_waivers: list[Waiver],
    renames: Optional[dict[str, str]] = None,
) -> Report:
    report = Report()
    waived = [f for f in head.findings if any(w.covers(f) for w in head_waivers)]
    judged = [f for f in head.findings if f not in waived]
    if base is None:
        report.old = sorted(judged, key=_place)
        return report
    before = {_moved(f, renames or {}).key for f in base.findings}
    # A package checked only at the end has no beginning to compare with: what it found is not new, it is unknown.
    # So are the routes, when the beginning had no frontend that could call them.
    unknown = set(base.failed_packages) - set(head.failed_packages)
    for f in judged:
        package_unknown = any(f.path == p or f.path.startswith(f"{p}/") for p in unknown)
        routes_unknown = f.kind == "route" and not base.routes_judged
        (report.old if f.key in before or package_unknown or routes_unknown else report.new).append(f)
    report.new.sort(key=_place)
    report.old.sort(key=_place)
    known = {(renames.get(w.path, w.path) if renames else w.path, w.name) for w in base_waivers}
    report.waivers_added = [w for w in head_waivers if (w.path, w.name) not in known]
    return report


def scan(repo: Path, base: Optional[str], head: str) -> Report:
    """The dead code of the commit ``head`` of ``repo``, split by what ``base`` already had; without ``base`` everything
    is old (the cleanup of a project).

    Raises:
        CheckNotRun: the commits cannot be read or there is no folder to read them into.
    """
    try:
        work = smoke_scratch.new_scratch()
    except smoke_scratch.ScratchUnavailable as exc:
        raise CheckNotRun(str(exc)) from exc
    try:
        trees = {"head": _export(repo, head, work / "head")}
        if base:
            trees["base"] = _export(repo, base, work / "base")
        packages = sorted({p for t in trees.values() for p in _packages(t)})
        not_run: list[str] = []
        script: dict[str, Any] = {}
        if packages:
            try:
                script = _run_script_half(repo, trees, packages)
            except CheckNotRun as exc:
                not_run.append(f"frontend a trasy sa neskontrolovali — {exc}")
        read = {
            name: _read_tree(tree, script.get(name, {}) if packages and not not_run else None)
            for name, tree in trees.items()
        }
        head_waivers, invalid = waivers(trees["head"])
        base_waivers = waivers(trees["base"])[0] if base else []
        renames = _renames(repo, base, head) if base else {}
        report = _judge(read["head"], read.get("base"), head_waivers, base_waivers, renames)
        report.invalid_waivers = invalid
        failed = sorted(read["head"].failed_packages.items())
        report.not_run = not_run + [f"priečinok {pkg} sa neskontroloval — {reason}" for pkg, reason in failed]
        return report
    finally:
        smoke_scratch.discard(work)


#: How many findings a sentence names — the fixer finds the rest by the same rule.
LISTED = 40


def listing(findings: list[Finding], limit: int = LISTED) -> Iterator[str]:
    """The sentences of the findings, the first ``limit`` of them, and how many more there are."""
    yield from (f.sentence() for f in findings[:limit])
    if len(findings) > limit:
        yield f"a ďalších {len(findings) - limit}"


#: How each finding is resolved — the sentence the agent reads in a stopped verdict and in the cleanup's fast fix.
HOW_TO_RESOLVE = (
    "Export, ktorý potrebuje len skúška, označ komentárom /** @internal */. Čo predsa volá niečo mimo kódu projektu "
    "(rámec, plánovač, iný systém), nechaj a zapíš výnimku s dôvodom do "
    f"{EXCEPTIONS_FILE} (položky path, name, reason)."
)


def cleanup_brief(findings: list[Finding]) -> str:
    """The fast fix that cleans up what earlier versions left behind — the text the Manažér reads, edits and starts."""
    lines = [
        f"Uprac kód, ktorý nikto nepoužíva — kontrola kokpitu ho našla v projekte z minulých verzií ({len(findings)}):",
        *(f"- {f.sentence()}" for f in findings),
        f"Každú z týchto vecí odstráň. {HOW_TO_RESOLVE}",
    ]
    return "\n".join(lines)
