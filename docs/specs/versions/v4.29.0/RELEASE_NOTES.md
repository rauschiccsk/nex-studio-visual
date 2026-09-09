# v4.29.0 — päť opráv z ostrej prevádzky

Všetkých päť vzniklo tak, že Director na niečo klikol a nefungovalo to — alebo to
fungovalo inak, než malo. Ani jedna nevznikla od stola.

## Poznámky k vydaniu už nikto neprepisuje pod rukami

NEX Studio si poznámky generuje z evidencie úloh a zapisuje ich do projektu. Robilo to
však aj tam, kde si ich projekt **napísal sám** — a prepísalo pekný zákaznícky text suchým
zoznamom, v tej istej sekunde, ako manažér schválil fázu.

Nezávislá previerka to dvakrát označila za rozrobenú prácu a stavbu zablokovala. Agent to
poslušne vrátil a pri ďalšej bráne sa to prepísalo znova. Bojoval s kokpitom a vyhrať
nemohol.

Generovaná poznámka odteraz nesie podpis. Bez podpisu a bez zhody s generátorom je to
cudzia práca a nesiaha sa na ňu — a povie sa to nahlas, lebo ticho by bolo vlastnou pascou.
Záchranná sieť ostáva pre projekty, ktoré si poznámky nepíšu.

## Prevzatému projektu sa už nevymýšľa verzia

Každý projekt dostával pri založení verziu „0.1.0". Pri prevzatí je to výmysel — projekt
svoju históriu má. A nebolo to len nepekné číslo: priečinok tej verzie v projekte často už
existuje a má vlastný obsah, takže by sa písalo do cudzích dokumentov.

Prevzatý projekt teraz verziu nedostane; prvú si založí manažér. Aby vedel, na čo nadväzuje,
prehliadka pred prevzatím mu ukáže poslednú verziu, ktorú o sebe projekt hovorí.

## Príznak živého náhľadu sa nedá tipnúť zle

Pieskovisko posielalo appke hodnotu, o ktorej nikde nestálo, ako ju má appka čítať. Štyri
projekty si to tipli správne, jeden nesprávne — a jeho náhľad sa vôbec nezapol. Manažér
z toho videl prihlasovaciu obrazovku a príčinu hľadal úplne inde.

Posiela sa hodnota, ktorá vyhovie obom zvyklostiam naraz. Trieda chyby tým zaniká.

## Kontrola dverí do nasadenej appky skúša tú cestu, po ktorej sa naozaj chodí

Skúšala starý spôsob spúšťania — ten, ktorý appky ďalej prijímajú. Svietila by teda
nazeleno aj vtedy, keby ten skutočne používaný spôsob nefungoval. Zelená stráž, ktorá
neskúša cestu, po ktorej sa chodí, je horšia než červená.

## Šablóna dáva novému projektu živý náhľad

Doteraz si ho musel každý projekt vymyslieť sám — a podľa toho, ako ho napísal, mu buď
fungoval, alebo skončil na prihlasovacej obrazovke. Fáza Vizuál je pritom to, po čom je celý
produkt pomenovaný.
