# v4.50.0

**Z požiadavky v Zásobníku vznikne tiket v Plane.** Pri požiadavke je tlačidlo „Založiť tiket“: kokpit ponúkne názov,
popis, druh práce, typ a zodpovedného, ktoré môžeš pred založením upraviť, a jedným klikom založí tiket v module
projektu v evidencii DEV. Požiadavka a tiket ostanú prepojené — pri požiadavke vidíš číslo tiketu s odkazom do Plane,
v tikete je REQ-N — a druhé kliknutie nezaloží druhý tiket. Tiket sa odvtedy posúva sám a ku každému posunu zapíše
komentár: zaradenie požiadavky do verzie ho dá do In Progress, nasadenie tej verzie na PROD do Done, zamietnutie do
Cancelled a vrátenie do Zásobníka späť do Todo. Zamietnutie si teraz pýta dôvod — ostane pri požiadavke a zapíše sa aj
do tiketu. Keď Plane práve neodpovie, zmena požiadavky sa aj tak uloží a kokpit povie, že tiket sa neposunul.

**Záložka „Plane tikety“.** V bočnej ponuke pri projekte pribudla záložka s tiketmi projektu v Plane (modul projektu
v evidencii DEV) — otvorené aj uzavreté, pri každom jeho stĺpec, typ, druh práce, kto na ňom robí, kedy sa naposledy
zmenil a odkaz do Plane. Tiket, ktorý vznikol zo Zásobníka, ukáže svoju požiadavku (REQ-N) a verziu, v ktorej je.

**Vizuál už nie je v bočnej ponuke.** Živý náhľad aplikácie počas kroku Vizuál otvoríš v Riadiacom centre tlačidlom
„Otvoriť živý náhľad“ — v samostatnom okne vedľa rozhovoru, v ktorom vidíš, čo treba vyskúšať.

**Živý náhľad vidí poznámky k vydaniam.** Aplikácia, ktorá v záložke „Aktualizácie“ ukazuje poznámky k vydaniam,
sa v živom náhľade pri štarte zastavila, lebo náhľad dostal len priečinok obrazovky. Teraz dostane aj poznámky k
vydaniam, len na čítanie — náhľad NEX Managera 1.3.0 sa tak spustí.
