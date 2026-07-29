# v4.0.82 — prevzatie ručne postavenej inštalácie sa dá vyžiadať

Kokpit vie sám postaviť inštaláciu appky. Odkedy má poistku proti prepísaniu
cudzieho nasadenia, odmieta siahnuť na priečinok, ktorý sám nevytvoril — a tým je
**trinásť inštalácií, ktoré na tomto stroji bežia**, vrátane ostrého MÁGERSTAV-u a
spoločného smerovača, cez ktorý ide 29 kontajnerov.

To je správne. Chýbala však cesta von: odmietnutie odkazovalo na vnútorné
nastavenie, ktoré sa z terminálu nedalo zapnúť, takže žiadnu z tých trinástich
inštalácií sa nedalo cez naše vlastné nástroje postaviť nanovo.

## Čo je nové

Prepínač `--adopt` pri nasadzovaní. Predvolene vypnutý, **len z terminálu**.
Odmietnutie teraz vypíše presný príkaz, ktorý treba napísať — aj s tým, čo to
stojí:

```
python scripts/uat-deploy.py <skratka> --adopt --dry-run   # najprv ukáž, čo by sa prepísalo
python scripts/uat-deploy.py <skratka> --adopt
```

Prepísaním sa do priečinka zapíše hlavička, a **práve tá rozhoduje**, či tam kokpit
smie písať. Od tej chvíle je ten priečinok bez tejto ochrany aj pri budúcich
nasadeniach — preto to odmietnutie hovorí nahlas.

**Tlačidlo „Nasadiť" v kokpite túto možnosť zámerne nemá.** Stráži to test, ktorý
číta priamo zdrojový kód, takže sa to nedá omylom pridať.

## Prečo nie hromadné prevzatie

Zvažovalo sa vpísať hlavičku do všetkých trinástich naraz. Tri nezávislé
posúdenia to zamietli, a všetky z rovnakého dôvodu: **hlavička nie je poznámka, je
to kľúč od zápisu.** Vpísať ju znamená trvalo a ticho udeliť právo prepisovať — a
zmazať jediný dôkaz, že ten súbor bol kedy písaný ručne.
