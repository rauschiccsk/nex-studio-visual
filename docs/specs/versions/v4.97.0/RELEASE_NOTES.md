# v4.97.0

**Inštaláciu UAT sa dá postaviť nanovo.** Pri zákazníkovi, ktorý má inštaláciu UAT, je vedľa „Nasadiť“ tlačidlo
„Postaviť nanovo“. Pred spustením okno povie, že sa inštalácia zruší aj s databázou a uloženými dátami, a pustí ďalej
až po výslovnom potvrdení. Zaznačené body skúšky v kokpite ostávajú. Hodí sa vtedy, keď je inštalácia pokazená a bežné
„Nasadiť“ ju neopraví, lebo opakované nasadenie nastavenia inštalácie zámerne nemení. Pri ostrej inštalácii sa táto
voľba neponúka a kokpit ju odmietne.

**Oprava: „nanovo“ teraz naozaj začína od nuly.** Doterajšia voľba v rozhraní API vymenila heslo databázy, ale staré
dáta s pôvodným heslom ponechala, takže aplikácia by sa k databáze nepripojila. Teraz sa inštalácia najprv zruší aj so
svojimi zväzkami a až potom sa postaví znova.
