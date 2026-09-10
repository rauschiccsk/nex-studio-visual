# v4.30.1 — Znalostná báza si už dokumenty nezdvojuje

Nález z vlastnej práce: pri zápise do Znalostnej bázy sa dokument uložil druhý raz vedľa
prvého, namiesto aby ten prvý nahradil.

## Preindexovanie nahradí, nepribudne

Preindexovanie najprv zmaže staré kúsky dokumentu a potom zapíše nové. Mazanie sa riadilo
označením, ktoré dostalo, ale zápis si označenie odvodil sám z cesty k súboru. Keď sa tie
dve líšili, nezmazalo sa nič a v korpuse ostali dve kópie — vyhľadávanie potom vracia ten
istý text niekoľkokrát a staré znenie žije ďalej vedľa nového.

Označenie dokumentu je teraz jedno a to isté pre obe strany.

## Cesta k súboru už nemení kategóriu

Kategória sa určuje z prvej časti cesty. Keď sa dokument pomenoval celou cestou na disku,
vyšla z toho kategória „home" namiesto „projects" — a filtrovanie podľa kategórie taký
dokument nenájde.

Dokument sa teraz volá rovnako, nech ho volajúci pomenuje celou cestou alebo cestou v rámci
bázy. Čo pod bázou neleží, sa neskracuje.

*(Zmerané naživo: štyri body so zlou kategóriou proti 3 720 správnym. V samotnom kokpite sa
to zatiaľ neprejavovalo — obe strany tam zhodou okolností posielali to isté označenie — ale
bola to pasca čakajúca na prvého volajúceho, ktorý to spraví inak.)*
