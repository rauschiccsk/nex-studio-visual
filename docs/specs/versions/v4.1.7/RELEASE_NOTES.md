# v4.1.7 — Vizuál sa presunul za zavreté dvere; zostáva jediná fáza

Zo série, ktorá postupne zatvára stavbu do vlastného priestoru — aby sa práca na
jednom projekte nemohla omylom dotknúť súborov druhého.

## Vizuál pribudol a nestálo to nič

Vizuál zostával vonku s odôvodnením, že stavia a spúšťa celú aplikáciu, a teda
potrebuje prístup k správe kontajnerov. **To odôvodnenie bolo nesprávne.**

Náhľad naozaj beží — ale spúšťa ho a drží pri živote **NEX Studio samo**, nie
pomocník. Ten vo Vizuáli iba upravuje zdroje obrazoviek a náhľad si zmenu
prevezme sám, do sekundy. Jeho zadanie mu pritom trikrát opakuje, že backend ani
dátové modely v tejto fáze robiť nemá.

Fáza teda nikdy nič také nepotrebovala — bola vonku preto, že tak **vyzerala**.
Presunutá bez novej súčiastky a bez jediného ústupku.

Zavreté sú tým **štyri fázy z piatich**: Príprava, Návrh, Vizuál, Programovanie.

## Verifikácia zostáva vonku — vedome

Posledná fáza je nezávislá previerka pred vydaním. Jej úlohou je appku
**spustiť a presvedčiť sa na vlastné oči**, či robí to, čo dokumentácia sľubuje —
nie veriť tomu, čo o sebe povie pomocník. Zobrať jej možnosť appku spustiť by
znamenalo buď oslabiť poslednú nezávislú kontrolu pred vydaním, alebo postaviť
novú, bezpečnostne krehkú súčiastku, ktorá by jej prístup sprostredkovala.

Je to zároveň fáza s **najmenším rizikom omylu** z celej päťky: previerka nikdy
nič nezapisuje ani neukladá — iba číta a skúša.

## Pokyny pre pomocníka sa zmenili spolu s tým

Pokyny dovtedy hovorili, že „postaviť a spustiť celú appku patrí do Vizuálu a
Verifikácie — tie fázy Docker majú". Pre Vizuál to prestalo platiť v ten istý
deň, tak sa aj prepísali — vo všetkých projektoch aj v šablóne pre nové.
Pravidlo, ktoré pomocník nevidí, nie je pravidlo, ale pasca; strážny test to
odteraz kontroluje strojovo.
