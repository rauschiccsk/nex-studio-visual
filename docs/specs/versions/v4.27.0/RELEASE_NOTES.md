# v4.27.0 — čakanie na cudzí stroj už kokpit nezhasne

Pokračovanie opravy zo 7. septembra, keď jeden ťah agenta na minúty zhasol celý kokpit.
Vtedy sa opravilo rozbehnutie živého náhľadu; otvorené zostali gitové príkazy v samotnom
engine.

## Čo ukázalo meranie

Než som prerábal 48 miest, zmeral som, čo tie príkazy naozaj stoja — päť meraní na
najväčšom repozitári:

| príkaz | čas |
|---|---|
| prečítanie aktuálneho commitu | 2,0 – 4,5 ms |
| stav pracovného stromu | 8,4 – 15,2 ms |
| počet commitov | 16,7 – 24,0 ms |
| zoznam zmenených súborov | 2,6 – 6,4 ms |

Milisekundy, nie minúty — a každý z nich má strop 15 sekúnd. Sú to čakania **miestne
a ohraničené**.

Jediné, ktoré siaha za hranicu tohto stroja, je **odosielanie vydania do vzdialeného
repozitára**: strop 60 sekúnd a závislosť od cudzej siete. To je presne ten druh čakania,
ktorý kokpit vie zhasnúť.

## Čo je opravené

Odosielanie vydania beží vo vlastnom vlákne. Sú **tri** miesta, kde sa spúšťa — a to
tretie by som bez novej stráže minul: strojové prehľadanie ho nenašlo, lebo sedelo
v obyčajnej funkcii, ktorú volajú až funkcie nad ňou.

Pri hľadaní sa našlo aj jediné volanie cudzieho programu v celom backende **bez stropu**:
pauza medzi pokusmi o kontrolu zdravia pri zakladaní projektu bola napísaná ako spustenie
programu `sleep`. Keby sa ten zasekol, čakalo by sa naveky. Teraz je to obyčajná pauza.

## Stráže

Dve nové, obe overené červené pred opravou:

- nič, čo čaká na cudzí stroj, nesmie bežať na hlavnej slučke;
- každý spustený program musí mať strop.

Druhá z nich je široká — platí na celý backend, nielen na tento jeden prípad.

## Čo som zámerne neurobil

Neprerobil som zvyšných 45 miestnych gitových volaní. Podľa meraní vyššie by to bola
rozsiahla zmena v najcitlivejšom súbore engine-u kvôli riziku, ktoré je ohraničené na
15 sekúnd a v praxi trvá milisekundy — a pri väčšine z nich by bola aj nesprávna: pracujú
s databázovým sedením, ktoré do cudzieho vlákna nepatrí.
