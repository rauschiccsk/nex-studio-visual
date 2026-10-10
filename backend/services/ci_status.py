"""Čo hovorí CI o commite — JEDNO pravidlo pre bránu aj pre obrazovku (ICCINT-129).

**Čo tomu predchádzalo.** Brána Verifikácie si pýtala od GitHubu jeden beh (``--limit 1``). Jeden
commit však spúšťa viac pracovných postupov — NEX Inbox má dva — takže brána dostala ten, ktorý sa
zaregistroval posledný, a o druhom sa nedozvedela. Hod mincou: 14.09.2026 prešiel NEX Inbox 1.5.0
do stavu Hotovo s padajúcim zostavením, pričom jedna z padajúcich skúšok strážila štítok, ktorý pri
úpravách vzhľadu ticho vypadol z obrazovky.

Tá časť je opravená. Tento modul rieši druhú polovicu tiketu: **Manažér má stav zostavenia vidieť
priebežne**, nie až na bráne — kto vidí červenú pri druhom commite, nedostane sa do stavu, že
prerába hotovú verziu.

⚠️ **Prečo to býva tu a nie dvakrát.** Brána a obrazovka odpovedajú na tú istú otázku („je ten commit
zelený?"), len v inej chvíli. Keby si každá vykladala „čo je červená" sama, raz sa rozídu a Manažér
uvidí zelenú tam, kde brána vidí červenú — tá istá chyba ako pôvodná, len o poschodie vyššie. Preto
je tu **pravidlo** (:func:`verdikt`) aj **dopyt** (:func:`prikaz_na_behy`); líši sa len to, čo ktorý
čitateľ robí s čakaním.

⚠️ **Brána čaká, obrazovka nie.** Brána si počká, kým beh vznikne a dobehne — pri rozhodovaní o vydaní
je to správne. Prehľad stavby sa obnovuje každých 25 sekúnd; čakajúci dopyt by ho pri každom otvorení
zavesil na minúty a vyzeralo by to, že zamrzol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

#: Závery, ktoré znamenajú, že beh o kóde povedal „v poriadku".
CI_CONCLUSIONS_OK = frozenset({"success", "skipped", "neutral"})

#: Závery, ktoré znamenajú, že beh ZLYHAL. ⚠️ Zámerne NIE „všetko ostatné": ``cancelled`` a ``stale``
#: o kóde nehovoria nič, a brána, ktorá zhodí verziu preto, že niekto stlačil Zrušiť, je brána,
#: ktorú sa ľudia naučia obchádzať.
CI_CONCLUSIONS_RED = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

#: Koľko behov si od GitHubu vypýtať. Ďaleko nad skutočným počtom na commit — ale nie neobmedzene:
#: neohraničený výstup zo stroja, ktorý neriadime, je vlastné riziko.
CI_RUNS_PER_COMMIT_LIMIT = 20

#: Ako dlho platí odpoveď pre OBRAZOVKU. Prehľad sa obnovuje každých 25 s; bez tejto pamäte by sa
#: GitHubu chodilo pýtať pri každom obnovení každého otvoreného prehľadu.
PAMAT_SEKUND = 60

_PRIKAZ_TIMEOUT = 20


def pomenuj_beh(row: dict) -> str:
    """Ako sa beh volá vo vete, ktorú číta človek.

    Meno postupu je rovnako dôležité ako číslo: 14.09.2026 brána ohlásila „CI zelené (beh 34869175048)"
    o behu ÚPLNE INÉHO postupu — jediné slovo, na ktoré sa Manažér spoliehal, bolo to nesprávne.
    """
    cislo = row.get("databaseId")
    postup = str(row.get("workflowName") or "").strip()
    return f"postup {postup}, beh {cislo}" if postup else f"beh {cislo}"


def bezi(rows: list[dict]) -> list[dict]:
    """Behy, ktoré ešte nedobehli — o kóde zatiaľ nehovoria nič."""
    return [r for r in rows if r.get("status") != "completed"]


def prvy_zlyhany(rows: list[dict]) -> Optional[dict]:
    """Prvý DOKONČENÝ beh, ktorý zlyhal. Poradie v zozname nič neznamená, takže ktorýkoľvek je odpoveď."""
    for row in rows:
        if row.get("status") == "completed" and row.get("conclusion") in CI_CONCLUSIONS_RED:
            return row
    return None


def verdikt(rows: list[dict]) -> tuple[str, str]:
    """Čo hovorí CI o commite — ``("green"|"red"|"unknown", veta pre človeka)``.

    ⚠️ **Toto je to jediné miesto, kde sa rozhoduje, čo je červená.** Číta ho brána aj obrazovka.

    ``red`` má prednosť pred všetkým ostatným: beh, ktorý UŽ padol, je odpoveď a čakanie na jeho
    súrodencov by červenú mohlo nechať rozpadnúť sa na ``unknown``, ktoré neblokuje.
    """
    zlyhal = prvy_zlyhany(rows)
    if zlyhal is not None:
        return "red", f"CI zlyhalo ({pomenuj_beh(zlyhal)}, {zlyhal.get('conclusion')})"

    bezia = [r for r in rows if r.get("status") != "completed"]
    if bezia:
        return "unknown", f"CI ešte beží ({', '.join(pomenuj_beh(r) for r in bezia)})"

    if not rows:
        return "unknown", "pre tento commit sa zatiaľ neobjavil žiadny beh CI"

    presli = [r for r in rows if r.get("conclusion") in CI_CONCLUSIONS_OK]
    if not presli:
        # Všetky behy skončili bez toho, aby o kóde niečo povedali — zrušené, zastarané. Nie je to
        # dôkaz zdravia ani dôkaz poruchy. `unknown` neblokuje, ale musí to POVEDAŤ.
        zavery = ", ".join(f"{pomenuj_beh(r)}: {r.get('conclusion')}" for r in rows)
        return "unknown", f"žiadny beh CI nedal výsledok ({zavery})"

    return "green", f"CI zelené ({', '.join(pomenuj_beh(r) for r in presli)})"


def prikaz_na_behy(repo: str, sha: str) -> list[str]:
    """Otázka pre GitHub — jedna pre bránu aj pre obrazovku.

    ⚠️ Zdieľa sa aj dopyt, nielen pravidlo. Keby si obrazovka pýtala iné stĺpce, rozhodovala by
    z menšieho obrazu — napríklad bez mena postupu, ktoré je pri červenej to podstatné.
    """
    return [
        "gh",
        "run",
        "list",
        "-R",
        repo,
        "--commit",
        sha,
        "--limit",
        str(CI_RUNS_PER_COMMIT_LIMIT),
        "--json",
        # DEV-51: ``headBranch`` + ``event`` say WHICH push started a run (a branch, or a version tag), so the gate
        # can tell the run its own tag push caused from older runs of the same commit; ``url`` lets the screen
        # send the Manažér straight to a failed run.
        "status,conclusion,databaseId,workflowName,headBranch,event,url",
    ]


# ── DEV-51: the runs a version tag push starts ───────────────────────────────


def _vzor_na_regex(vzor: str) -> re.Pattern[str]:
    """A GitHub Actions ref filter pattern as a regex: ``**`` any characters, ``*`` any but ``/``, ``?``/``+``
    the preceding character's quantifiers, ``[...]`` a character class — everything else literal."""
    out, i = [], 0
    while i < len(vzor):
        if vzor.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        ch = vzor[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch in "?+":
            out.append(ch)
        elif ch == "[":
            end = vzor.find("]", i)
            if end == -1:
                out.append(re.escape(ch))
            else:
                out.append(vzor[i : end + 1])
                i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _zodpoveda_filtru(ref: str, vzory: Any) -> bool:
    """Whether ``ref`` passes a GitHub ``tags``/``branches`` filter list — later patterns win, ``!`` negates."""
    if isinstance(vzory, str):
        vzory = [vzory]
    vysledok = False
    for vzor in vzory or []:
        vzor = str(vzor)
        neguj = vzor.startswith("!")
        if _vzor_na_regex(vzor[1:] if neguj else vzor).match(ref):
            vysledok = not neguj
    return vysledok


def _push_spusti_znacku(push: Any, tag: str) -> bool:
    """Whether a workflow's ``on.push`` block starts a run when ``tag`` is pushed (GitHub's own rules)."""
    if push is None:
        return True  # ``push:`` with no filters — every branch and every tag
    if not isinstance(push, dict):
        return False
    if "tags" in push:
        return _zodpoveda_filtru(tag, push["tags"])
    if "tags-ignore" in push:
        return not _zodpoveda_filtru(tag, push["tags-ignore"])
    # Only branch filters → the workflow does not run for tags. Neither (only ``paths``) → it runs for both;
    # path filters are not evaluated for tag pushes.
    return "branches" not in push and "branches-ignore" not in push


def postupy_spustene_znackou(project_root: Path, tag: str) -> list[str]:
    """Names of the project's workflows that a push of ``tag`` starts — what the gate must wait for (DEV-51).

    10.10.2026, NEX Inbox 1.7.0: the cockpit pushed the version tag ``v1.7.0`` and asked GitHub about the commit
    two seconds later. Two green runs from an hour before were already there, so the gate declared "CI zelené";
    the run the tag started (``Release smoke gate`` on ``push: tags: ['v*']``) registered two seconds after
    the question and failed. The name is what ``gh run list`` reports as ``workflowName``: the workflow's
    ``name``, or its file path when it has none. A file that cannot be read is skipped and logged — the gate
    then cannot wait for it, which is the old behaviour, not a new hole."""
    adresar = project_root / ".github" / "workflows"
    if not adresar.is_dir():
        return []
    mena = []
    for subor in sorted([*adresar.glob("*.yml"), *adresar.glob("*.yaml")]):
        try:
            data = yaml.safe_load(subor.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("workflow %s sa nedá prečítať — brána naň nepočká: %s", subor, exc)
            continue
        if not isinstance(data, dict):
            continue
        on = data.get("on", data.get(True))  # YAML 1.1 reads a bare ``on:`` key as True
        if isinstance(on, str):
            spusti = on == "push"
        elif isinstance(on, list):
            spusti = "push" in on
        elif isinstance(on, dict):
            spusti = "push" in on and _push_spusti_znacku(on["push"], tag)
        else:
            spusti = False
        if spusti:
            mena.append(str(data.get("name") or f".github/workflows/{subor.name}"))
    return mena


def chybajuce_behy_znacky(rows: list[dict], tag: str, postupy: list[str]) -> list[str]:
    """Which of ``postupy`` has no run yet for the push of ``tag`` (``headBranch`` is the tag for a tag push)."""
    mame = {str(r.get("workflowName") or "") for r in rows if r.get("headBranch") == tag}
    return [p for p in postupy if p not in mame]


@dataclass(frozen=True)
class StavZostavenia:
    """Čo o poslednom zostavení vieme povedať Manažérovi."""

    stav: str
    detail: str
    #: Commit, o ktorom to platí. Bez neho by veta na obrazovke nemala o čom byť.
    sha: Optional[str] = None
    #: Kedy sme sa pýtali. Odpoveď je stará najviac :data:`PAMAT_SEKUND`.
    zistene_o: float = 0.0
    #: DEV-51: niektorý beh ešte nedobehol — nasadenie počká, kým dobehne.
    bezi: bool = False
    #: DEV-51: odkaz na zlyhaný (alebo ešte bežiaci) beh — nech Manažér nehľadá, ktorý to bol.
    url: Optional[str] = None


_pamat: dict[str, tuple[float, StavZostavenia]] = {}


def _zabudni_vsetko() -> None:
    """Len pre skúšky — pamäť je procesová a medzi skúškami by sa prenášala."""
    _pamat.clear()


def _bez_cakania(prikaz: list[str], cwd: Optional[Path] = None) -> tuple[int, str]:
    try:
        r = subprocess.run(prikaz, capture_output=True, text=True, timeout=_PRIKAZ_TIMEOUT, check=False, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{exc.__class__.__name__}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


async def _behy_z_githubu(project_root: Path, repo: str, sha: str) -> tuple[Optional[list[dict]], Optional[str]]:
    """Behy pre commit — bez čakania. ``([], None)`` znamená „spýtal som sa, beh ešte nie je"."""
    rc, out = await asyncio.to_thread(_bez_cakania, prikaz_na_behy(repo, sha), project_root)
    if rc != 0:
        return None, f"stav CI sa nepodarilo zistiť ({out.strip()[:100]})"
    try:
        rows = json.loads(out)
    except ValueError:
        return None, "odpoveď o behoch CI sa nedá prečítať"
    return (rows if isinstance(rows, list) else []), None


def _repo_z_remote(url: str) -> Optional[str]:
    text = url.strip().removesuffix(".git")
    for sep in ("github.com/", "github.com:"):
        if sep in text:
            part = text.split(sep, 1)[1].strip("/")
            return part if part.count("/") == 1 else None
    return None


async def _zisti(project_root: Path, sha: Optional[str], teraz: float) -> StavZostavenia:
    """Stav CI pre ``sha`` (``None`` = HEAD projektu) — bez pamäte, bez čakania, nikdy nepadá."""
    if not sha:
        rc, head = await asyncio.to_thread(_bez_cakania, ["git", "-C", str(project_root), "rev-parse", "HEAD"])
        sha = head.strip() if rc == 0 else ""
    if not sha:
        return StavZostavenia("unknown", "HEAD projektu sa nepodarilo zistiť", None, teraz)

    rc, remote = await asyncio.to_thread(
        _bez_cakania, ["git", "-C", str(project_root), "config", "--get", "remote.origin.url"]
    )
    repo = _repo_z_remote(remote) if rc == 0 else None
    if not repo:
        return StavZostavenia("unknown", "projekt nemá čitateľný vzdialený repozitár", sha, teraz)

    rows, chyba = await _behy_z_githubu(project_root, repo, sha)
    if chyba:
        return StavZostavenia("unknown", chyba, sha, teraz)
    rows = rows or []

    stav, detail = verdikt(rows)
    zlyhal = prvy_zlyhany(rows)
    beziace = bezi(rows)
    odkaz = (zlyhal or (beziace[0] if beziace else None) or {}).get("url")
    return StavZostavenia(stav, detail, sha, teraz, bezi=stav != "red" and bool(beziace), url=odkaz)


async def stav_commitu(project_root: Path, sha: Optional[str] = None, *, cerstvy: bool = False) -> StavZostavenia:
    """Čo hovorí CI o commite ``sha`` (``None`` = HEAD) — pre obrazovku aj pre nasadenie (DEV-51).

    Odpoveď si pamätá :data:`PAMAT_SEKUND`; ``cerstvy=True`` sa spýta GitHubu vždy — tak sa pýta samotné
    nasadenie, kde rozhoduje, nie len ukazuje. **Nikdy nečaká a nikdy nepadá:** keď sa nedá zistiť čokoľvek
    z reťaze, vráti ``unknown`` s vetou, ktorá hovorí PREČO. ⚠️ Nevedomosť sa nesmie tváriť ako dobrá správa."""
    kluc = f"{project_root}@{sha or 'HEAD'}"
    teraz = time.time()
    zapamatane = _pamat.get(kluc)
    if not cerstvy and zapamatane is not None and teraz - zapamatane[0] < PAMAT_SEKUND:
        return zapamatane[1]
    v = await _zisti(project_root, sha, teraz)
    _pamat[kluc] = (teraz, v)
    return v


async def snapshot(project_root: Path) -> StavZostavenia:
    """Okamžitý obraz stavu zostavenia HEAD projektu pre obrazovku. **Nikdy nečaká a nikdy nepadá.**"""
    return await stav_commitu(project_root)
