# v4.21.1 — Schválenie Vizuálu sa už nezasekne na rozpore, ktorý nikto nepomenoval

Druhá polovica včerajšieho zaseknutia. Prvá bola v strope konzultácií (v4.21.0),
táto je pod ňou.

## Čo sa dialo

Agent pri premietaní vizuálu do dokumentov označí rozpor **jedným slovom** a má ho
potom vymenovať. Keď slovo napísal, ale zoznam nechal prázdny, **kokpit si rozpor
vymyslel** — uložil položku s textom *„(rozpor bez popisu)"*.

Schválenie potom vždy odbočilo do vetvy s rozporom. Ťah, ktorý z neho mal spraviť
rozhodovaciu kartu, správne odmietol vymýšľať si obsah — a stavba sa vrátila presne
tam, kde bola. Každý ďalší klik to zopakoval.

Nedalo sa rozhodnúť, lebo nebolo o čom.

## Čo sa mení

**Značka bez obsahu je odpoveď v zlom tvare, nie rozpor.** Schválenie prejde a
dostaneš vetu, ktorá povie presne toto: dokumenty sú premietnuté, agent označil
rozpor a žiadny nevymenoval, prezri si ich.

Zastavovať sa na tom nedalo pomôcť — a mlčať by bolo horšie.

## Poznámka pre nás

Toto správanie bolo **zapísané v skúške ako správne**: „agent povedal rozpor a nič
nevymenoval → zastaviť stavbu". Znie to opatrne a bola to slepá ulička. Skúška je
prepísaná aj s dôvodom, prečo pôvodné znenie bolo mylné.
