"""DEV-386 — an error a service raises and a route hands to a person reads Slovak, and its kind goes by its type.

14.11.2026, measured while DEV-385 turned the cockpit's own error texts Slovak: the services raised 107 English error
texts (``ValueError``) — „Customer with slug … already exists in this project“, „Current password is incorrect“ — and
the routes passed them to the screen; nine route modules decided 404 / 409 by English words in the text („not found“,
„already exists“, „blocked“ …), one Slovak text even carried the word „Conflict:“ only to get its 409. Director, on the
ticket and on doing it: „Súhlasím, založ tiket a hneď implementuj“.

A rule, not a list. The boundary is the code rule: text a person reads is Slovak, an error that never leaves the code
stays English. Which is which is measured, not judged: every service function a route reaches from a block that turns a
``ValueError`` into the screen's text (transitively — module functions, imported names, methods through ``self`` and
through a parameter or helper typed by a service class) — and every error raised there is read for English with the
rule of the cockpit's other language guards (``tests/test_the_manazer_is_told_in_slovak.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.test_the_manazer_is_told_in_slovak import _text, is_english

BACKEND = Path(__file__).resolve().parents[1] / "backend"

CAUGHT = {"ValueError", "Exception", "BaseException"}


def _modname(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND.parent).with_suffix("").parts)


def _index():
    """module → {function or Class.method → node}, and every module's imports: alias → module, name → (module, name)."""
    funcs, imports, classes = {}, {}, {}
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts:
            continue
        mod = _modname(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        f, imp = {}, {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                f[node.name] = node
            elif isinstance(node, ast.ClassDef):
                classes[(mod, node.name)] = node
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        f[f"{node.name}.{item.name}"] = item
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend"):
                for a in node.names:
                    target = f"{node.module}.{a.name}"
                    if (BACKEND.parent / Path(*target.split("."))).with_suffix(".py").exists():
                        imp[a.asname or a.name] = ("module", target)
                    else:
                        imp[a.asname or a.name] = ("name", node.module, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("backend"):
                        imp[a.asname or a.name.split(".")[-1]] = ("module", a.name)
        funcs[mod], imports[mod] = f, imp
    return funcs, imports, classes


def _catches_value_error(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    names = []
    if t is None:
        return True
    for n in t.elts if isinstance(t, ast.Tuple) else [t]:
        names.append(n.id if isinstance(n, ast.Name) else getattr(n, "attr", ""))
    return any(n in CAUGHT for n in names)


def _shows_text(handler: ast.ExceptHandler) -> bool:
    """The handler turns the error into what the screen shows — an HTTPException or a mapper of errors."""
    for n in ast.walk(handler):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", getattr(n.func, "attr", ""))
            if name == "HTTPException" or name.startswith("_map_") or name == "http_error":
                return True
    return False


def _calls(node: ast.AST, mod: str, funcs, imports, classes, local_types: dict[str, tuple[str, str]], cls: str | None):
    """(module, function key) of what ``node`` calls, as far as it can be told statically."""
    imp = imports[mod]
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            if f.id in funcs[mod]:
                yield mod, f.id
            elif f.id in imp and imp[f.id][0] == "name":
                _, m, name = imp[f.id]
                if m in funcs and name in funcs[m]:
                    yield m, name
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            owner = f.value.id
            if owner in imp and imp[owner][0] == "module" and imp[owner][1] in funcs:
                m = imp[owner][1]
                if f.attr in funcs[m]:
                    yield m, f.attr
            elif owner == "self" and cls and f"{cls}.{f.attr}" in funcs[mod]:
                yield mod, f"{cls}.{f.attr}"
            elif owner in local_types:
                m, c = local_types[owner]
                if f"{c}.{f.attr}" in funcs.get(m, {}):
                    yield m, f"{c}.{f.attr}"


def _local_types(fn: ast.AST, mod: str, imports, funcs=None) -> dict[str, tuple[str, str]]:
    """Parameters and locals typed by a service class: ``manager: KnowledgeManager``, ``m = KnowledgeManager(…)`` or
    ``m = _get_manager()`` where the helper's return annotation names the class."""
    imp, out = imports[mod], {}

    def cls_of(node):
        name = node.id if isinstance(node, ast.Name) else None
        if name and name in imp and imp[name][0] == "name":
            return imp[name][1], imp[name][2]
        return None

    for a in getattr(fn, "args", ast.arguments(args=[], posonlyargs=[], kwonlyargs=[])).args + fn.args.kwonlyargs:
        if a.annotation is not None and (c := cls_of(a.annotation)):
            out[a.arg] = c
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)):
            continue
        c = cls_of(n.value.func)
        helper = funcs.get(mod, {}).get(getattr(n.value.func, "id", "")) if funcs else None
        if c is None and helper is not None and helper.returns is not None:
            c = cls_of(helper.returns)
        if c:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = c
    return out


def _reachable():
    """Every (module, function) a route's error-to-text block reaches, and the index it was found in."""
    funcs, imports, classes = _index()
    start = set()
    for mod, f in funcs.items():
        if not mod.startswith("backend.api"):
            continue
        for key, fn in f.items():
            types = _local_types(fn, mod, imports, funcs)
            for node in ast.walk(fn):
                if isinstance(node, ast.Try) and any(_catches_value_error(h) and _shows_text(h) for h in node.handlers):
                    for stmt in node.body:
                        start.update(_calls(stmt, mod, funcs, imports, classes, types, None))
    seen, todo = set(), list(start)
    while todo:
        mod, key = todo.pop()
        if (mod, key) in seen or mod not in funcs or key not in funcs[mod]:
            continue
        seen.add((mod, key))
        fn = funcs[mod][key]
        cls = key.split(".")[0] if "." in key else None
        todo.extend(_calls(fn, mod, funcs, imports, classes, _local_types(fn, mod, imports, funcs), cls))
    return seen, funcs


def _value_error_classes(funcs) -> set[str]:
    """``ValueError`` and every class of the backend built on it — what the routes' blocks catch."""
    names = {"ValueError"}
    changed = True
    trees = [ast.parse(p.read_text(encoding="utf-8")) for p in BACKEND.rglob("*.py") if "tests" not in p.parts]
    while changed:
        changed = False
        for tree in trees:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ClassDef)
                    and node.name not in names
                    and any(getattr(b, "id", getattr(b, "attr", "")) in names for b in node.bases)
                ):
                    names.add(node.name)
                    changed = True
    return names


def _reachable_raises():
    seen, funcs = _reachable()
    classes = _value_error_classes(funcs)
    out = []
    for mod, key in sorted(seen):
        if not mod.startswith("backend.services"):
            continue
        for node in ast.walk(funcs[mod][key]):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                name = getattr(node.exc.func, "id", getattr(node.exc.func, "attr", ""))
                if name in classes and (text := _text(node.exc.args[0])):
                    out.append((f"{mod.removeprefix('backend.services.')}:{node.lineno} {key}", " ".join(text.split())))
    return seen, out


def test_the_reach_is_seen():
    """A blind reach passes everything — it must see the routes' services, the class-typed ones too."""
    seen, raises = _reachable_raises()
    assert len(seen) >= 400 and len(raises) >= 100, (len(seen), len(raises))
    places = {place.split(" ")[1] for place, _ in raises}
    assert {"authenticate_user", "KnowledgeManager._validate_category", "change_password"} <= places, places


def test_no_error_a_route_hands_to_a_person_is_english():
    _seen, raises = _reachable_raises()
    english = [f"{place}: {text[:110]}" for place, text in raises if is_english(text)]
    assert english == [], "a person reads the cockpit in Slovak — the error a route shows them too:\n  " + "\n  ".join(
        english
    )


def _text_of_the_error(fn: ast.AST) -> set[str]:
    """Names that hold the caught error's text in ``fn``: ``str(exc)`` and what is derived from it (``.lower()``)."""
    held: set[str] = set()
    for _ in range(2):
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                v = n.value
                if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "str":
                    held.add(n.targets[0].id)
                elif isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute):
                    if isinstance(v.func.value, ast.Name) and v.func.value.id in held:
                        held.add(n.targets[0].id)
    return held


def test_no_route_decides_an_errors_kind_by_its_words():
    """The kind (404 / 409 / …) goes by the error's type — a route that reads it from the text breaks the moment the
    text is said in another language. Exempt: a database constraint's name in an ``IntegrityError``, which is no text
    for a person."""
    decided = []
    for path in sorted((BACKEND / "api").rglob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            held = _text_of_the_error(fn)
            for n in ast.walk(fn):
                if not (
                    isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str)
                ):
                    continue
                for op, right in zip(n.ops, n.comparators):
                    reads = (
                        (isinstance(right, ast.Name) and right.id in held)
                        or (isinstance(right, ast.Call) and getattr(right.func, "id", "") == "str")
                        or (  # message.lower() — a method of what holds the error's text
                            isinstance(right, ast.Call)
                            and isinstance(right.func, ast.Attribute)
                            and isinstance(right.func.value, ast.Name)
                            and right.func.value.id in held
                        )
                        or (  # str(exc).lower()
                            isinstance(right, ast.Call)
                            and isinstance(right.func, ast.Attribute)
                            and isinstance(right.func.value, ast.Call)
                            and getattr(right.func.value.func, "id", "") == "str"
                        )
                    )
                    if isinstance(op, ast.In) and reads:
                        decided.append(f"{path.relative_to(BACKEND.parent)}:{n.lineno}: {n.left.value!r}")
    assert decided == [], "the kind of an error goes by its type (backend/core/errors.py):\n  " + "\n  ".join(decided)


def test_the_kind_goes_by_the_type():
    from backend.core.errors import ConflictError, NotFoundError, http_error

    assert http_error(NotFoundError("Zákazník neexistuje.")).status_code == 404
    assert http_error(ConflictError("Zákazník už v tomto projekte je.")).status_code == 409
    assert http_error(ValueError("Customer not found — already exists")).status_code == 422
    assert http_error(ValueError("Hodnota nie je číslo."), default=400).status_code == 400
    assert http_error(ConflictError("Už existuje.")).detail == "Už existuje."
