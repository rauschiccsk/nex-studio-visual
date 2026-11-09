# v4.121.1

**Nasadenie do existujúcej inštalácie stavia aplikáciu tak, ako ju stavia aktuálna verzia projektu.** Doteraz si
inštalácia pri každom ďalšom nasadení ponechala spôsob stavby z prvého nasadenia; zmena stavby v novej verzii projektu
sa do nej nedostala. NEX Websites 1.1.0 preto na UAT zlyhal — po prechode na šablónu aplikácie jeho backend pri stavbe
číta aj priečinky `frontend` a `docs`, no inštalácia stavala po starom. Teraz každá služba, ktorú projekt stavia,
dostane stavbu z nasadzovanej verzie. Čo patrí inštalácii — mená, siete, zväzky s dátami, premenné a porty — ostáva
nezmenené.
