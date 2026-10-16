# v4.0.53 — NEX Studio Visual

## Nové aplikácie testujú databázu na skutočnom PostgreSQL

Doteraz NEX Studio generoval aplikáciám testy, ktoré bežali na odľahčenej databáze (SQLite v pamäti), kým samotná aplikácia v prevádzke používa PostgreSQL. Ten rozdiel spôsobil, že automatické testy na buildovacom serveri zlyhali hneď pri prvej zmene v backende (napríklad pri zázname spustenia webu — taký zápis vie iba PostgreSQL).

Odteraz každá generovaná aplikácia testuje svoju backendovú logiku proti **skutočnému PostgreSQL** — rovnakému, na akom beží naostro. Chyby sa tak chytia verne a build nezlyhá na rozdiele medzi testovacou a ostrou databázou.

## Zjednotené nastavenie spustenia z NEX Managera (UAT „Spustiť")

Názvy nastavení, cez ktoré NEX Manager aplikáciu otvára, sú teraz zjednotené podľa štandardu (`MANAGER_LAUNCH_SIGNING_KEY`, `MANAGER_MODULE_SLUG`, `MANAGER_DEPLOY_SLUG`). Nová aplikácia ich odteraz deklaruje správne od začiatku, takže tlačidlo **„Spustiť"** v UAT funguje hneď po nasadení — bez ručného dolaďovania.
