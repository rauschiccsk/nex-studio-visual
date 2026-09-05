# v4.9.0 — Rýchla oprava sa konečne dá dokončiť

Toto je oprava najzávažnejšej chyby, akú sme v NEX Studiu zatiaľ mali.

**Rýchla oprava nemohla prejsť overením. Nikdy. Ani keby bol kód dokonalý.**

## Čo sa dialo

Pred vydaním sa spúšťa automatická skúška, ktorá overí, že appka naozaj robí to, čo
sľubuje. Aby vedela **čo** má vyskúšať, potrebuje zoznam kľúčových vecí, ktoré má
vydanie predviesť. Ten zoznam vzniká vo fáze **Návrh**.

Rýchla oprava ale Návrh **nemá** — to je jej zmysel, ide krátkou cestou. Takže zoznam
neexistoval, skúška hlásila „niet čo predviesť, vydanie sa nedá overiť", a Verifikácia
verziu neprepustila.

A keďže neúspech zakladá opravnú úlohu, agent musel zakaždým niečo robiť. Chyba pritom
nebola v appke — tak vyrábal výplň. Auditor hlásil „Bez nálezu", engine dal znova
neúspech, a šlo ďalšie kolo.

Na projekte NEX ProductCatalogs to bežalo **22 hodín, 16 kôl a 91 ťahov agenta** — pri
štvorbodovej oprave. Auditor pritom v jednom z posudkov napísal doslova: *„Projekt je
hotový a preverený — AI Agent už nemá čo opraviť."*

## Čo sa mení

Rýchla oprava si ten zoznam **povie sama, vo fáze, ktorú má** — v Príprave, a **pred**
tým, než sa začne opravovať. Agent odvodí zo zadania jednu vetu o tom, čo má byť po
oprave vyskúšateľné na bežiacej appke, a skúška si prah zoberie odtiaľ.

## Čo sa nemení — a to je dôležité

**Latka zostáva rovnako vysoko.** Prah sa len presunul tam, kde ho tá dráha vie splniť;
neznížil sa. Rýchla oprava, ktorá nepovie nič, je naďalej neoveriteľná a neprejde —
máme na to test, aby sa táto oprava nedala neskôr omylom zmeniť na vypnutú bránu.

Bežná dráha (nová verzia) číta zoznam ďalej z Návrhu, presne ako doteraz. Aj na to je
test, aby sa Príprava nestala zadnými dverami okolo Návrhu.

## Prečo to prešlo do produkcie

Neexistoval test, že rýchla oprava vie dôjsť až do konca. Overovali sme, že sa dráha
posúva medzi fázami — nie že sa **dá dokončiť**. Ten test teraz existuje.
