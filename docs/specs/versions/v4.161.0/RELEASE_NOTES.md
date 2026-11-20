# v4.161.0

**Náhľad Vizuálu ukazuje knižnice, ktoré sú naozaj nainštalované.** Keď verzia povýši šablónu či nex-shared, náhľad
sa už nespustí na starých knižniciach: pred štartom kokpit zladí nainštalované balíky so zoznamom balíkov, a keď ich
agent počas Vizuálu nainštaluje znova, náhľad sa postaví nanovo sám. Predtým mohol náhľad ukazovať staré obrazovky,
hoci povýšenie v projekte bolo. Po nasadení sa bežiace náhľady raz postavia nanovo.

**Lišta náhľadu povie, že sa náhľad pripravuje.** Po spustení náhľadu lišta v Riadiacom centre ukazuje „Náhľad sa
pripravuje…“, kým náhľad naozaj neodpovie, a odkaz ponúkne až potom. Keď to trvá dlhšie než zvyčajne, povie to a
ponúkne náhľad spustiť znova. Vizuál sa dovtedy neschvaľuje — kokpit povie, že treba počkať.

**Prílohy namiesto „Súbory pre Staviteľa“.** Zoznam pod rozhovorom má nadpis „Prílohy“ a počíta len súbory, ktoré si
priložil ty. Pracovné súbory Staviteľa sú pod nimi zvlášť, zbalené, s počtom súborov.

**Zmena Pravidiel agenta sa dostane aj k agentovi, ktorý už pracuje.** Doteraz dostal agent pravidlá len na začiatku
svojej relácie a tá trvá naprieč verziami. Keď sa pravidlá zmenia, najbližší ťah agenta dostane ich aktuálne znenie —
raz za zmenu.
