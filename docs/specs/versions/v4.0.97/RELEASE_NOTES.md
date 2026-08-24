# v4.0.97 — stavba prestala vidieť cudzie projekty

## Stavba býva zavretá vo vlastnom projekte

Stavba dovtedy bežala priamo v kontajneri aplikácie — a ten má na zápis
pripojené súbory všetkých zákazníkov a beží s najvyššími právami. Stavba pre
jedného zákazníka teda videla a mohla prepísať ostré súbory druhého.

Nešlo o obranu pred útočníkom. Šlo o **omyl nedozeraného pomocníka**, ktorý sa
pomýli v ceste a zapíše tam, kde nemal.

Príprava a Návrh idú odteraz do jednorazového priestoru, ktorý vidí **výhradne
svoj projekt** a beží bez zvýšených práv. Po skončení sa zahodí.

Je to prvá polovica. Programovanie, Vizuál a Verifikácia sa v tomto vydaní
nemenili — stavajú celú aplikáciu a potrebujú viac než len svoj projekt.

## Znalostná báza je vnútri čitateľná, nie prepisovateľná

Do zavretého priestoru sa spolu s projektom dostala aj spoločná znalostná báza,
ale **len na čítanie** — vrátane vyhľadávania v nej, nielen otvárania súborov.
Polovica, ktorá dovolí súbor otvoriť, ale nie prehľadať, by bola tá istá pasca,
len menšia.

Zapisovať do spoločnej bázy zvnútra stavby naďalej nejde.

## Zostava rolí v pokynoch zodpovedá skutočnosti

Dokument, ktorý si každý pomocník číta na začiatku práce, popisoval zostavu
troch samostatných rolí spúšťaných príkazmi, ktoré sa reálne nepoužívajú.
Zmerané: dve z tých troch rolí takto nebežali **ani raz**, tretia naposledy
začiatkom júna.

Produkt medzitým pracuje s jedným pomocníkom a nezávislou previerkou. Text sa
zosúladil so skutočnosťou — na piatich miestach, kde sa naň odvolával.
