# v4.98.1

**Oprava: Programovanie dostane aj databázu s rozšírením pgvector.** Projekt, ktorý vyhľadáva podľa významu, potrebuje
PostgreSQL s rozšírením pgvector. Kokpit pri programovaní spúšťal z bezpečnostných dôvodov len oficiálny obraz
PostgreSQL a takýto projekt zastavil na prvej úlohe s databázou ako chybu kokpitu. Teraz spustí aj oficiálny obraz
`pgvector/pgvector` — postavený na tom istom PostgreSQL, len s rozšírením navyše. Iné obrazy databázy kokpit ďalej
nespustí a pri odmietnutí povie, ktorý obraz to bol.
