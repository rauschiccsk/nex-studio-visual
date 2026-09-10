# v4.31.0 — prístupové údaje sa do vyhľadávania už nedostanú

Nález z prehliadky korpusu Znalostnej bázy. Neotváral som obsah a neuvádzam z neho nič.

## Dokument s prístupmi sa odmietne zaindexovať

Vo vyhľadávacom indexe ležali dva kúsky dokumentu z priečinka `credentials/` — hoci ten
priečinok na disku už dávno nie je. Kópia prežila zmazanie originálu.

Vyhľadávanie aj čítanie dokumentu túto kategóriu bežným kontám skrývajú, takže o otvorený
únik nešlo. Ale clona pri čítaní je obrana na nesprávnom konci: zakrýva niečo, čo v úložisku
už leží — a leží tam ticho, lebo v prehliadači súborov ten dokument nikto nevidí.

Indexovanie taký dokument odteraz **odmietne**, ešte než sa čokoľvek zapíše. Kontroluje sa
odvodené označenie, nie surová cesta, aby zábranu neobišiel iný tvar tej istej cesty.

Zároveň je zapísané pravidlo, ktoré tie dve rozhodnutia drží pokope: **čo sa pri čítaní
skrýva, to sa nesmie ani indexovať.** Keby niekto pridal kategóriu medzi skryté a zabudol na
indexovanie, obsah by sa do úložiska ďalej zapisoval.

*(Tie dva už zapísané kúsky som zámerne nezmazal a čakajú na rozhodnutie: keď súbor na disku
nie je, index môže byť jeho posledná kópia.)*
