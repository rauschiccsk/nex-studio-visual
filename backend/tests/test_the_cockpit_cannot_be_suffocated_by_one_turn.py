"""Jeden ťah agenta nesmie zadusiť celý kokpit (ICCINT-74).

**Čo sa stalo.** 07.09.2026 o 19:33 prestal backend odpovedať na čokoľvek — Manažér nenačítal v NEX
Studiu vôbec nič. Žiadna chyba, žiadna výnimka, posledný zápis v protokole z času tesne pred
zaseknutím. Jedinou stopou bola hromada zaseknutých kontrol zdravia vnútri kontajnera: 16 po dvadsiatich
minútach, 65 po štyridsiatich. Docker kontajner označil ako nezdravý, ale nikomu to nič nepovedalo.
Obnovilo to až reštartovanie backendu.

**Príčina.** ``_run_vizual_round`` je ``async``, ale ``vizual_sandbox.spin_up`` je obyčajná funkcia,
ktorá spúšťa ``npm install`` (strop 600 s) a dva dockerové príkazy (po 60 s). Zavolaná priamo z ``async``
funkcie beží na **hlavnej slučke**, takže celý ten čas server nestíha odbaviť ani ``/health``. Nie je to
chyba v zmysle pádu — je to normálna práca na nesprávnom vlákne.

**Prečo tu stojí strojová stráž a nie odporúčanie.** Toto sa nedá ustrážiť disciplínou: ten istý omyl
sa pri ďalšej funkcii spraví znova a prejaví sa až o mesiac ako „NEX Studio nejde“. Nižšia stráž preto
prehľadá celý backend a nájde každé miesto, kde ``async`` funkcia priamo volá niečo, čo čaká na proces.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import time

import pytest

from backend.core.offload import BlockingWorkTimedOut, run_blocking

#: Moduly, ktoré spúšťajú vonkajšie procesy. Práca v nich čaká na disk, sieť alebo dockerovho démona —
#: teda presne to, čo sa nesmie diať na hlavnej slučke.
#: Zoznam sa NEVYPISUJE ručne — dopĺňa ho stráž nižšie, ktorá ho porovná so skutočnosťou. Pri prvom
#: písaní bol ručný a hneď mu chýbali dva moduly.
SHELLING_MODULES = {
    "build_db",
    "build_sandbox",
    "claude_agent",
    "consult_sandbox",
    "create_project_postscaffold",
    "git_state",
    "nexshared",
    "notify",
    "orchestrator",
    "port_registry",
    "system_setting",
    "template_bootstrap",
    "uat_provisioner",
    "vizual_sandbox",
}

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _sync_functions_that_wait(module: str) -> set[str]:
    """Synchrónne funkcie modulu, ktoré (aj cez suseda v tom istom module) spúšťajú proces."""
    path = BACKEND / "services" / f"{module}.py"
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local: dict[str, tuple[bool, set[str], bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls: set[str] = set()
        waits = False
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            short = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            full = (
                f"{func.value.id}.{func.attr}"
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                else short
            )
            if short:
                calls.add(short)
            if full in {"subprocess.run", "subprocess.check_output", "subprocess.call", "subprocess.Popen"}:
                waits = True
        local[node.name] = (isinstance(node, ast.FunctionDef), calls, waits)

    changed = True
    while changed:  # rozšír „čaká“ cez volania v rámci modulu, kým sa množina ustáli
        changed = False
        for name, (is_sync, calls, waits) in list(local.items()):
            if not waits and any(local.get(c, (False, set(), False))[2] for c in calls):
                local[name] = (is_sync, calls, True)
                changed = True
    return {name for name, (is_sync, _calls, waits) in local.items() if is_sync and waits}


def _offenders() -> list[str]:
    waiting = {m: _sync_functions_that_wait(m) for m in SHELLING_MODULES}
    found: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — cudzí súbor, nie náš problém
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                func = getattr(call, "func", None)
                if not (isinstance(call, ast.Call) and isinstance(func, ast.Attribute)):
                    continue
                if not isinstance(func.value, ast.Name):
                    continue
                if func.attr in waiting.get(func.value.id, set()):
                    rel = path.relative_to(BACKEND.parent)
                    found.append(f"{rel}:{call.lineno} — async {node.name}() volá {func.value.id}.{func.attr}()")
    return sorted(set(found))


def test_no_async_function_waits_for_a_process_on_the_main_loop() -> None:
    """⚠️ Jadro ICCINT-74 — a jediná stráž, ktorá tomu vie zabrániť do budúcna.

    Keď táto stráž spadne, nie je to „upratovanie kódu“: znamená to, že niekto pridal volanie, ktoré
    dokáže na minúty zhasnúť celý kokpit. Riešenie je vždy to isté — poslať tú prácu cez
    :func:`backend.core.offload.run_blocking` do vlákna a dať jej strop.

    **Čo táto stráž ZATIAĽ nekryje, a treba to vedieť.** Pozerá sa na volania NAPRIEČ modulmi
    (``vizual_sandbox.spin_up`` a spol.) — teda tam, kde býva dlhá práca. Nepozerá sa na volania
    v rámci orchestrátora samotného: tie sú písané holým menom (``_repo_head(...)``) a je ich 46.
    Sú to gitové príkazy so stropmi 15 s, pri odosielaní vydania 60 s — čiže tá istá choroba,
    ale rádovo kratšia. Vlastný tiket **ICCINT-82** s presnými číslami; nemieša sa sem zámerne,
    lebo 46 zmien v najcitlivejšom súbore engine-u by sa nedalo poriadne overiť spolu s touto opravou.
    """
    offenders = _offenders()
    assert offenders == [], (
        "práca, ktorá čaká na proces, patrí do vlákna (run_blocking), nie na hlavnú slučku:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_actually_finds_such_a_call() -> None:
    """⚠️ Stráž nad strážou: prehľadávanie, ktoré nikdy nič nenájde, je zelené aj v horiacom dome.

    Overuje, že rozpoznávanie funguje na tej funkcii, ktorá 07.09.2026 kokpit reálne položila.
    """
    assert "spin_up" in _sync_functions_that_wait("vizual_sandbox"), (
        "prehľadávanie nerozpozná ani tú funkciu, ktorá kokpit reálne položila"
    )


def test_the_scan_looks_everywhere_a_process_is_started() -> None:
    """⚠️ Pri prvom písaní mala táto stráž presne tú dieru, ktorú stráži.

    Zoznam modulov bol vypísaný ručne. Keď som ho pri skúške naschvál oslepil — vyhodil
    ``vizual_sandbox``, teda ten jediný modul, cez ktorý kokpit spadol — stráž zostala **zelená**.
    Ručný zoznam je stráž, ktorá si sama vyberá, kam sa pozrie.

    Preto sa zoznam porovnáva so skutočnosťou: každý modul služby, ktorý spúšťa proces, v ňom musí byť.
    Nový modul tak nemôže vzniknúť mimo dohľadu — buď sa doplní, alebo je táto stráž červená.
    """
    spusta_proces = {
        path.stem for path in (BACKEND / "services").glob("*.py") if "subprocess." in path.read_text(encoding="utf-8")
    }
    chyba = spusta_proces - SHELLING_MODULES
    assert chyba == set(), "tieto moduly spúšťajú proces, ale prehľadávanie sa do nich vôbec nepozerá: " + ", ".join(
        sorted(chyba)
    )


@pytest.mark.asyncio
async def test_the_main_loop_keeps_answering_while_the_work_waits() -> None:
    """Meranie, nie viera: kým práca vo vlákne čaká, hlavná slučka stíha ďalšie kolá.

    Práve toto v ten večer neplatilo — server nestihol odbaviť ani vlastnú kontrolu zdravia.
    """
    ticks = 0

    async def tikanie() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    tikac = asyncio.create_task(tikanie())
    try:
        await run_blocking(time.sleep, 0.3, cap=5)
    finally:
        tikac.cancel()
    assert ticks >= 5, f"slučka počas čakania takmer nebežala (kôl: {ticks})"


@pytest.mark.asyncio
async def test_work_that_never_ends_gives_the_loop_back() -> None:
    """Strop existuje preto, aby ťah nečakal donekonečna a v kokpite naveky nesvietilo „pracuje sa“.

    Vlákno sa zabiť nedá a beží ďalej — podstatné je, že volajúci sa dozvie, že sa nedočkal, a vie
    stav usadiť a povedať to Manažérovi.
    """
    with pytest.raises(BlockingWorkTimedOut):
        await run_blocking(time.sleep, 5, cap=0.05)
