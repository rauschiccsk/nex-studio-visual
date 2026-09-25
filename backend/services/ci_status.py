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
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
        "status,conclusion,databaseId,workflowName",
    ]


@dataclass(frozen=True)
class StavZostavenia:
    """Čo o poslednom zostavení vieme povedať Manažérovi."""

    stav: str
    detail: str
    #: Commit, o ktorom to platí. Bez neho by veta na obrazovke nemala o čom byť.
    sha: Optional[str] = None
    #: Kedy sme sa pýtali. Odpoveď je stará najviac :data:`PAMAT_SEKUND`.
    zistene_o: float = 0.0


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


async def snapshot(project_root: Path) -> StavZostavenia:
    """Okamžitý obraz stavu zostavenia pre obrazovku. **Nikdy nečaká a nikdy nepadá.**

    Keď sa nedá zistiť čokoľvek z reťaze (HEAD, vzdialený repozitár, odpoveď GitHubu), vráti
    ``unknown`` s vetou, ktorá hovorí PREČO. ⚠️ Nevedomosť sa nesmie tváriť ako dobrá správa.
    """
    kluc = str(project_root)
    teraz = time.time()
    zapamatane = _pamat.get(kluc)
    if zapamatane is not None and teraz - zapamatane[0] < PAMAT_SEKUND:
        return zapamatane[1]

    def _uloz(v: StavZostavenia) -> StavZostavenia:
        _pamat[kluc] = (teraz, v)
        return v

    rc, head = await asyncio.to_thread(_bez_cakania, ["git", "-C", str(project_root), "rev-parse", "HEAD"])
    sha = head.strip() if rc == 0 else ""
    if not sha:
        return _uloz(StavZostavenia("unknown", "HEAD projektu sa nepodarilo zistiť", None, teraz))

    rc, remote = await asyncio.to_thread(
        _bez_cakania, ["git", "-C", str(project_root), "config", "--get", "remote.origin.url"]
    )
    repo = _repo_z_remote(remote) if rc == 0 else None
    if not repo:
        return _uloz(StavZostavenia("unknown", "projekt nemá čitateľný vzdialený repozitár", sha, teraz))

    rows, chyba = await _behy_z_githubu(project_root, repo, sha)
    if chyba:
        return _uloz(StavZostavenia("unknown", chyba, sha, teraz))

    stav, detail = verdikt(rows or [])
    return _uloz(StavZostavenia(stav, detail, sha, teraz))
