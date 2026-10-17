# v4.52.1

**Nasadenie na PROD na inom stroji sa už nezastaví na dobrej zálohe.** Kokpit pred nasadením zálohuje databázu
a zálohu si overí. Doteraz ju na overenie posielal z cieľového stroja cez seba a späť a pri databáze väčšej než
pár megabajtov vyhlásil aj úplne dobrú zálohu za nečitateľnú — tak sa dvakrát zastavilo nasadenie NEX Inbox 1.9.0
pre MÁGERSTAV. Záloha sa teraz overuje priamo na cieľovom stroji, kde leží, a keď overenie zlyhá, hláška povie
aj prečo.
