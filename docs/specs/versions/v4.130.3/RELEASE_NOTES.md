# v4.130.3

**Kontrola zdravia po založení projektu už neplaší.** Po založení kokpit aplikáciu postaví, spustí a opýta sa jej na
zdravie. Doteraz sa pýtal na adrese servera zvnútra svojho kontajnera, kam nedosiahne — odpoveď nemohla prísť nikdy
a každý projekt založený na šablóne dostal upozornenie „aplikácia sa pravdepodobne nerozbehla“ (Orthodox Register,
NEX Legal Watch). Teraz sa backendu pýta zvnútra jeho vlastných kontajnerov, rovnako ako Verifikácia, na jeho porte
a ceste zdravia. Upozornenie sa ukáže len vtedy, keď aplikácia naozaj neodpovie, a povie, čo kokpit videl.
