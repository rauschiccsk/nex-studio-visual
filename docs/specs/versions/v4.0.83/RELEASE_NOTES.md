# v4.0.83 — projekt patrí tomu, kto ho založil

Zjednodušenie prístupových práv. Doteraz sa právo počítalo z troch vecí naraz —
z role, z vlastníctva, a podľa toho, či je úkon „citlivý". Tri premenné, ktoré sa
navzájom ovplyvňovali, sa nedali udržať v hlave a stále v nich vznikali diery.

Odteraz je otázka jedna: **je to jeho projekt?**

## Nové pravidlo, celé

- **Vlastník projektu je ten, kto ho založil.** Na svojom projekte smie **všetko**
  — bez stupňov, bez výnimiek, bez „toto ešte áno, toto už nie".
- **Kto projekt nevlastní, ten ho ani neuvidí.** Nie je v zozname.
- **Účet `admin` vidí a smie všade.** Je to konkrétny účet, nie rola.
- Dvaja ľudia môžu pracovať pod jedným prihlásením; na projekt tak pripadá práve
  jeden používateľ.

## Znalostná báza sa nemení

Roly Shu-Ha-Ri riadia **len** Znalostnú bázu, správu používateľov a trezor
prístupov. O projektoch nerozhodujú vôbec.

Jeden dôsledok: Junior má pod **vlastným** prihlásením v Znalostnej báze `icc/` a
`shuhari/`, dokumentáciu projektov nie. Pri spoločnom prihlásení to nevadí — číta
ako jeho Manažér.

## Čo pri tom vyšlo najavo

**Potvrdenie testovacej verzie — krok, ktorý otvára ostrú prevádzku — sa vôbec
nepýtalo, či je to tvoj projekt.** Držala ho len kontrola roly. Teraz sa
vlastníctvo overuje výslovne.

**Stĺpec, ktorý sa volá „owner", nie je vlastník.** Je to cieľ telegramových
oznámení. Políčko pri zakladaní projektu sa preto prestalo volať „Vlastník" a
hovorí, čím naozaj je: *Komu chodia správy od agenta*.

**V rozhraní teraz existuje jedno jediné miesto, kde je napísané, kto je admin.**
Predtým to bolo preklepané na štyroch miestach a na dvoch ďalších znamenal ten istý
zápis niečo iné — Znalostnú bázu. Kto by to prepísal hromadne, ticho by rozšíril
prístup k dokumentácii.

## Rozsah

404 preverených miest, 45 zmenených súborov, 854 zmazaných riadkov.
Zrušené aj „členstvo v projekte" spolu s jeho tabuľkou — v novom modeli nemá dôvod
existovať. Testy: 2778 zelených, pokrytie 90 %.
