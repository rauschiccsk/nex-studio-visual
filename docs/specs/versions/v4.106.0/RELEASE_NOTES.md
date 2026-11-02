# v4.106.0

**Pri nasadení dostane súbory aplikácie používateľ, pod ktorým nová verzia beží.** Keď aplikácia zmení používateľa
(NEX Knowledge a Author Asistent prechádzajú na rovnakého ako šablóna aplikácií), kokpit pred spustením novej verzie
odovzdá jej dátové priečinky a zväzky tomuto používateľovi — inak by do nich nová verzia nezapísala. Mení sa len to, čo
patrí niekomu inému; aplikácia pod správcovským účtom (root) a dáta databáz ostávajú bez zmeny. Čo sa zmenilo, povie
nasadenie jednou vetou; keď to nejde, nasadenie sa zastaví a bežiaca verzia slúži ďalej.
