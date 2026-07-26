# v4.0.50 — NEX Studio Visual

## „Moje konto" je teraz v Nastaveniach — vedľa „Používatelia"

Doteraz bolo **„Moje konto"** v ľavom paneli. Presunuli sme ho **do Nastavení** ako záložku **vedľa „Používatelia"** — správa účtu tak sídli systematicky na jednom mieste.

- **„Používatelia"** (len správca) — správa všetkých používateľov.
- **„Moje konto"** (každý používateľ) — **to isté, ale len tvoje údaje**: meno, e-mail, Telegram chat ID a zmena hesla. Prihlasovacie meno a rola sa zobrazujú, ale nemenia (tie rieši správca v „Používatelia").

Zo sidebaru „Moje konto" zmizlo; staré odkazy na `/account` presmerujú do Nastavení.

Riešenie je zapečené do zdieľanej knižnice `nex-shared`, takže **rovnakú záložku „Moje konto" dostanú aj ostatné ICC aplikácie** — systematicky, bez duplicity.
