# v4.7.1 — pozastavená stavba už nevymýšľa dôvod

Poslal si agentovi opravu, stavba sa pozastavila a napísal si mi, že niečo nie je
v poriadku. **Nebolo.** Stavba pokojne čakala, kým potvrdíš tú opravu, ktorú si sám
poslal.

Ale žltý pruh ti povedal toto:

> Stavba pozastavená **(token-limit)** — pokračuj tlačidlom nižšie.

Žiadny limit sa nedosiahol.

## Prečo to tak bolo

Stavba sa dá pozastaviť **tromi spôsobmi** a systém ku každému drží pravdivý dôvod:

| kedy | čo systém naozaj vie |
|---|---|
| oprava čaká na tvoje potvrdenie | „Oprava podľa tvojho pokynu je pripravená — 'Pokračovať' ju spustí." |
| skutočný limit tokenov | „Pozastavené — build prekročil token-limit (12 mil.)…" |
| pozastavil si ju ty | „Pozastavené Manažérom — pokračuj cez 'Pokračovať'." |

Pruh sa pýtal len na to, **či** je stavba pozastavená — a dôvod si dopísal sám, vždy
ten istý. V dvoch prípadoch z troch teda hovoril nepravdu.

## Čo sa mení

**Pruh zobrazí dôvod zo stavby.** Ten, ktorý tam systém uložil.

Zlepší sa tým aj ten prípad, kde bol text náhodou správny: pravdivá hláška o limite
**menuje konkrétny strop v miliónoch**, kým tá natvrdo napísaná nie.

A keď dôvod chýba, appka si ho **nevymyslí** — povie len, že stavba je pozastavená.
Radšej menej, než nepravdu.

## Ešte niečo, čo stojí za zmienku

Ten nepravdivý text **strážil test**. Bola tam kontrola, ktorá overovala, že sa na
obrazovke objaví práve „(token-limit)" — takže tá lož nebola prehliadnutie, bola
zamknutá. Test teraz pripína to, čo naozaj platí: že pruh nesie dôvod zo stavby.

---

*Prečo posledná číslica: opravuje sa chybné hlásenie. Nič nové sa neučíš, len prestaneš
narážať na nepravdu.*
