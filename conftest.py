"""Repo-root conftest — applies to BOTH ``tests/`` and ``backend/tests/``.

``pyproject.toml`` sets ``testpaths = ["tests", "backend/tests"]`` and pytest walks up
from each test file for conftest.py, so a fixture defined in either suite's own conftest
is invisible to the other. Anything that must hold for the whole run belongs here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.dependencies import get_knowledge_base_writer
from backend.config.settings import settings
from backend.main import app
from backend.services import template_bootstrap
from backend.services.knowledge_base_writer import KnowledgeBaseWriter


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path_factory, monkeypatch):
    """Keep the suite off the LIVE KB port registry.

    ``reserved_ranges_status`` reads ``/home/icc/knowledge/infrastructure/port-registry.yaml``
    (ICCINT-2). Left alone, tests depend on which blocks ICC has allocated in real life —
    the registry reserved 10110-10159 for NEX Automat and 10180-10189 for NEX Asistent, and
    a dozen tests that had picked ports in those ranges turned red for a reason that had
    nothing to do with what they were testing. Worse, they would turn red again on any
    future allocation, at a moment unrelated to the change that triggered it.

    Pointing at an absent file makes the service fall back to the ``reserved_port_ranges``
    setting, which is what the existing suite already controls per-test.

    The one test that WANTS the real file (``backend/tests/test_port_registry_reservations``
    ::test_live_registry_protects_the_block_that_was_handed_out) sets the attribute back
    explicitly, so the opt-in is visible rather than a silent skip.
    """
    from backend.services import port_registry

    absent = tmp_path_factory.mktemp("no-registry") / "port-registry.yaml"
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", absent)


#: Skutočný pracovný priečinok. Počas skúšok doň nesmie ukazovať ŽIADNY modul.
_SKUTOCNY_PROJECTS_ROOT = Path("/opt/projects")


def _moduly_s_cestou_na_projekty():
    """Moduly `backend.services`, ktoré si pamätajú vlastnú kópiu cesty k projektom.

    ⚠️ Hľadá sa PRAVIDLOM, nie zoznamom. Predošlá podoba izolácie vymenúvala dva moduly — a vlastnú
    kópiu ``Path("/opt/projects")`` má šesť. 26.09.2026 tak pri behu skúšok pribudlo do skutočného
    priečinka trinásť projektov; zapisoval ich ``version.write_zadanie`` cez ``_PROJECTS_ROOT``,
    o ktorom izolácia nevedela. Zoznam sa rozíde pri siedmom module; pravidlo nie.
    """
    import importlib
    import pkgutil

    import backend.services as balik

    najdene = []
    for info in pkgutil.iter_modules(balik.__path__):
        try:
            modul = importlib.import_module(f"backend.services.{info.name}")
        except Exception:  # noqa: BLE001 — modul, ktorý sa nedá načítať, nemá ako zapisovať
            continue
        for meno in dir(modul):
            if "PROJECTS_ROOT" not in meno.upper():
                continue
            if getattr(modul, meno, None) == _SKUTOCNY_PROJECTS_ROOT:
                najdene.append((modul, meno))
    return najdene


@pytest.fixture(scope="session", autouse=True)
def _isolate_projects_root(tmp_path_factory):
    """Presmeruje cestu k projektom do dočasného priečinka pre CELÝ beh — v OBOCH skúšobniach.

    **Prečo to je tu a nie v `tests/conftest.py`.** ``pyproject.toml`` má dve cesty ku skúškam
    (``tests`` a ``backend/tests``) a pytest hľadá conftest smerom NAHOR od každého súboru — takže
    fixtúra v jednej skúšobni je pre druhú neviditeľná. Izolácia tam bola a skúška, ktorá odpad
    vyrába (``backend/tests/test_metrics_phase_stamp.py``), býva v tej druhej. Od júla tak do
    skutočného priečinka pribudlo 54 priečinkov ``metrics-phase-<hex>`` (ICCINT-157).

    **A prečo sa moduly hľadajú, nie vymenúvajú** — viď :func:`_moduly_s_cestou_na_projekty`.

    Per-test ``monkeypatch.setattr(<modul>, "PROJECTS_ROOT", ...)`` naďalej prebíja; toto len
    odsúva PREDVOLENÚ hodnotu preč od skutočného priečinka.
    """
    tmp = tmp_path_factory.mktemp("projects_root")
    povodne = [(modul, meno, getattr(modul, meno)) for modul, meno in _moduly_s_cestou_na_projekty()]
    for modul, meno, _stara in povodne:
        setattr(modul, meno, tmp)
    yield tmp
    for modul, meno, stara in povodne:
        setattr(modul, meno, stara)


# ⚠️ ICCINT-157: TÁTO fixtúra býva v KOREŇOVOM conftest, aby ju videli OBE skúšobne.
# Dovtedy bola v `tests/conftest.py` a `backend/tests/` do nej nevidí — preto jeho skúšky
# zakladania projektu chodili po skutočnom `/opt/projects` a nechávali tam odpad.
# Zámerne NIE autouse: skúšky, ktoré čítajú Znalostnú bázu, si ju nastavujú samy. Prihlasuje sa
# cez `pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")`.


@pytest.fixture()
def _isolate_create_project_kb(tmp_path, monkeypatch):
    """Redirect the Create-Project flow's KB writes to an ISOLATED tmp KB.

    ``POST /api/v1/projects`` has bootstrap side-effects (the ``init.sh``
    subprocess, the :class:`KnowledgeBaseWriter`) that otherwise land dirs
    under the SHARED ``/home/icc/knowledge/projects/<slug>/`` — the ghost
    scaffold dirs cleaned by hand 2026-06-13 + 2026-07-09. Isolation, not
    clean-up: point the KB root at ``tmp_path`` so nothing touches the real KB
    even on a mid-test crash, and force ``init.sh`` into ``dry_run`` so its
    subprocess performs no ``/opt/projects`` or KB filesystem writes regardless
    of whether ``template_init_script_path`` is configured in this environment.

    Neutralises all three ghost vectors:
      1. ``settings.knowledge_base_path`` → tmp (``get_knowledge_base_writer``
         reads it at call time).
      2. ``get_knowledge_base_writer`` DI on the shared app → a tmp-rooted
         writer (belt-and-suspenders; modules that mount the router on their
         OWN app also override this on that app).
      3. ``invoke_init_script`` → dry-run (the historical ghost vector).

    Doubles as a live regression sentinel: snapshots the real KB ``projects``
    dir before the test and asserts NO new dir appeared there afterwards (the
    exact ghost-dir check the fix targets).
    """
    # Capture the REAL KB projects dir BEFORE we monkeypatch settings.
    real_kb_projects = Path(settings.knowledge_base_path) / "projects"
    before = {p.name for p in real_kb_projects.iterdir()} if real_kb_projects.is_dir() else set()

    kb_root = tmp_path / "knowledge"
    (kb_root / "projects").mkdir(parents=True)

    # (1) Settings-rooted KB access (``get_knowledge_base_writer`` reads this at
    #     call time) + (2) belt-and-suspenders DI override of the writer itself.
    monkeypatch.setattr(settings, "knowledge_base_path", str(kb_root))
    app.dependency_overrides[get_knowledge_base_writer] = lambda: KnowledgeBaseWriter(kb_root)

    # (3) init.sh — the historical ghost vector. Force dry-run so the subprocess
    #     never writes to /opt/projects or the KB even if the init script path is
    #     configured. Patched on the route module because it imports
    #     ``invoke_init_script`` by value (binding-by-value), so patching the
    #     source module would not rebind the route's reference.
    real_invoke = template_bootstrap.invoke_init_script

    def _dry_run_invoke(db, project, **kwargs):
        kwargs.setdefault("dry_run", True)
        return real_invoke(db, project, **kwargs)

    monkeypatch.setattr("backend.api.routes.projects.invoke_init_script", _dry_run_invoke)

    yield kb_root

    app.dependency_overrides.pop(get_knowledge_base_writer, None)

    after = {p.name for p in real_kb_projects.iterdir()} if real_kb_projects.is_dir() else set()
    new_dirs = after - before
    assert not new_dirs, (
        f"Create-Project test polluted the real KB {real_kb_projects}: {sorted(new_dirs)} — "
        "KB isolation broke (docs/specs/kb-ghost-root-cause.md Fix 1 / kb-ghost-followup.md Fix A)."
    )
