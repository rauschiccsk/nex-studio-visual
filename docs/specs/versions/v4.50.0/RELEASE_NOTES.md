# v4.50.0

**Z požiadavky v Zásobníku vznikne tiket v Plane.** Pri požiadavke je tlačidlo „Založiť tiket“: kokpit ponúkne názov,
popis, druh práce, typ a zodpovedného, ktoré môžeš pred založením upraviť, a jedným klikom založí tiket v module
projektu v evidencii DEV. Požiadavka a tiket ostanú prepojené — pri požiadavke vidíš číslo tiketu s odkazom do Plane,
v tikete je REQ-N — a druhé kliknutie nezaloží druhý tiket. Tiket sa odvtedy posúva sám a ku každému posunu zapíše
komentár: zaradenie požiadavky do verzie ho dá do In Progress, nasadenie tej verzie na PROD do Done, zamietnutie do
Cancelled a vrátenie do Zásobníka späť do Todo. Zamietnutie si teraz pýta dôvod — ostane pri požiadavke a zapíše sa aj
do tiketu. Keď Plane práve neodpovie, zmena požiadavky sa aj tak uloží a kokpit povie, že tiket sa neposunul.
