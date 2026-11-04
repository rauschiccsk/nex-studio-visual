# v4.109.3

**„Uložiť ich“ uloží neuložené zmeny aj v projekte, ktorý súbory kokpitu ignoruje.** Kokpit pri ukladaní vynechával
pravidlá agentov a zručnosti tak, že ich priečinky menoval ako výnimky. Projekt, ktorý ich má v `.gitignore` (napríklad
NEX Knowledge), git odmietol a karta hlásila „Uloženie zmien zlyhalo — git add zlyhal“; novú verziu ani rýchlu opravu
sa potom nedalo založiť. Teraz kokpit uloží presne tie súbory, ktoré karta ukazuje — vymenované jednotlivo, vrátane
zmazaných a premenovaných.
