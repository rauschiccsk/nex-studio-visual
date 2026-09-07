# v4.18.0 — Dokončená rýchla oprava už nevyzerá ako nezačatá

Verzia, ktorá prešla celou stavbou, sa v prehľade tvárila, že sa na nej ešte
nezačalo pracovať.

## Čo sa dialo

Stavba má dva konce: buď podpíšeš Hotovo ty, alebo si rýchla oprava na plnej
automatike podpíše sama. Zaznamenávanie dokončenia bolo zapojené **len do toho
prvého**.

Takže každá rýchla oprava dobehla, urobila prácu, prešla overením — a v evidencii
ostala v stave „rozpracovaná". Na NEX ProductCatalogs ich tak viselo **sedem**.

## Čo sa mení

Oba konce idú cez jedno miesto, ktoré dokončenie zapíše. Tretí koniec, keby raz
pribudol, to zdedí namiesto toho, aby si to musel niekto pamätať.

## Poznámka k tomu, prečo to nikto nezachytil

Stráž na to existovala — ale kontrolovala vetu v zdrojovom texte **ručného**
podpisu. Tá veta tam bola celý čas, aj keď sedem verzií viselo. Dnes sa žiada, aby
oba konce naozaj išli cez to spoločné miesto, a beh rýchlej opravy sa v skúške
doslova prejde až do konca a overí sa stav verzie.
