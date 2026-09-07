# v4.14.1 — Plán úloh povie, na čom sa pracuje; verzia neprejde s červenými kontrolami

Dve opravy. Prvá je o tom, čo v paneli vidíš; druhá o tom, čo panel dovolí vyhlásiť za hotové.

## Prvá — Plán úloh povie, na čom sa pracuje

Pri rýchlej oprave stálo v Pláne úloh na všetkých troch riadkoch
*„(bez ľudského vysvetlenia)"* — hoci zadanie si napísal ty sám a text ležal
o jedno políčko vedľa.

## Čo sa mení

Epika, funkcia aj úloha rýchlej opravy teraz nesú **tvoju vlastnú smernicu**.
V paneli tak vidíš, na čom sa robí, nie tri prázdne riadky pod sebou.

Nie je to strojom vymyslený text — je to presne to, čo si napísal.

## Prečo to bolo prázdne

Panel číta jedno konkrétne políčko. Vypĺňa ho AI Agent pri všetkom, čo sám
naplánuje, a opravné kolá po Verifikácii ho dostali už skôr. Rýchla oprava bola
posledná cesta, kde sa položky zakladajú strojovo — a tam to nikto nedoplnil.

## Prečo na tom záleží

Plán úloh je jediné miesto, kde vidíš, čo sa práve deje. Pri rýchlej oprave —
ktorá je celá o tom rýchlo pochopiť, čo sa opravuje — ti trikrát za sebou
oznamoval, že vysvetlenie nie je.

## Druhá — verzia už neprejde ako overená, keď sú kontroly projektu červené

Túto poistku sme zaviedli v predchádzajúcom vydaní, ale bola zapojená **len do
jednej cesty z troch** — do tej, kde výrok potvrdzuješ ty na zastávke. Cestu,
ktorou verzie prechádzajú v drvivej väčšine — keď si to Auditor odsúhlasí sám —
obchádzala.

Ukázalo sa to hneď: verzia 0.1.6 projektu NEX ProductCatalogs prešla ako overená
a v jej protokole nie je po kontrole ani stopa. Nebola červená — ale nikto sa
nepýtal.

### Čo sa mení

Poistka sa presunula do miesta, ktorým prechádzajú **obe** cesty. A keď zrazí
súhlas, zapíše si o tom vlastný výrok — to je dôležité, lebo bránu na Hotovo
otvára obsah **posledného** výroku. Bez toho by verzia ostala zablokovaná v stave,
ale podpis by ju prepustil.

Zelené kontroly sa odteraz zapisujú tiež — **s číslom behu**. Doteraz sa nedalo
rozoznať „kontroly boli zelené" od „nikto sa nepýtal"; oboje vyzeralo rovnako.

### Čo to znamená prakticky

Keď je CI projektu červené, verzia sa nedá vyhlásiť za overenú **žiadnou** cestou —
ani automatickou. Keď stav kontrol nevieme zistiť (beh ešte neexistuje, GitHub
neodpovedá), verzia prejde: neznalosť nie je dôkaz o chybe a brána, ktorá zastavuje
na neznalosti, sa naučí obchádzať.
