# v4.43.24

**Nasadenie na server zákazníka (napríklad MÁGERSTAV) nechá inštaláciu v poriadku aj vtedy, keď posledná kontrola
neprejde.** Doteraz kokpit pri takom zlyhaní vrátil na server predpis starej verzie, hoci nová už bežala a databáza bola
prerobená pre ňu. Po najbližšom reštarte by sa aplikácia vrátila na starú verziu. Teraz sa predpis vracia len vtedy,
keď sa nová verzia vôbec nespustila. Keď beží, ostane a kokpit povie, ktorá kontrola neprešla. V riadku PROD je potom
verzia, ktorá naozaj beží, spolu s upozornením, že posledný pokus zlyhal; akceptovať sa taká verzia nedá.

**Kontrola verejnej adresy pýta správny server a správnu adresu.** Pri inštalácii na inom serveri sa kokpit pýta
adries, ktoré má inštalácia nastavené vo svojom smerovaní (napríklad magerstav-inbox.icc.sk), tak, ako ich otvára
používateľ. Doteraz sa pýtal vlastného servera na adresu, ktorú si poskladal sám, a hlásil chybu, hoci aplikácia
bežala.

**Kratší výpadok pri nasadení.** Obrazy novej verzie sa stavajú, kým stará verzia beží ďalej; aplikácia je nedostupná
až pri výmene kontajnerov. Počas nasadenia vidno, kedy sa to deje: „staviam obrazy — bežiaca inštalácia sa zatiaľ
nemení“ a potom „spúšťam novú verziu — aplikácia je teraz chvíľu nedostupná“.
