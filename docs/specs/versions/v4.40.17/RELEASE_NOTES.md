# v4.40.17

Náhľad pred prevzatím predpovedá to, čo sa naozaj stane.

**Čo bolo zlé.** Predchádzajúce vydanie vrátilo prevzatie na bežnú cestu — inštalácii, ktorá už stojí, sa mení verzia a nie stavba. Náhľad však stále počítal podľa zdrojového projektu, takže hlásil stratu služieb a úložiska, ktoré by v skutočnosti nikam nezmizli. Aplikácia, ktorá si svoje časti pomenovala po svojom, sa tak nedala prevziať, hoci jej nič nehrozilo.

**Teraz predpovedá a zapisuje ten istý kód.** Keď inštalácia stojí, náhľad počíta povýšením verzie — presne ako zápis. Keď v priečinku ešte žiadny popis nie je, počíta zo zdrojového projektu, lebo niet čo povyšovať.
