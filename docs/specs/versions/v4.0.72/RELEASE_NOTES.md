# v4.0.72 — NEX Studio Visual

## Zakladanie nového projektu opäť funguje

Od 27. júla sa **nedal založiť žiadny nový projekt**. Formulár sa vyplnil, po kliknutí prišla hláška o chybe servera a takto to skončilo vždy. Príčinou bola úprava v inom repozitári: zo zakladacieho skriptu sa v ten deň odstránila zrušená rola aj s jej prepínačom, ale kokpit ten prepínač naďalej posielal. Skript ho nepoznal a odmietol pracovať.

Opravené. Zakladanie projektov je znovu funkčné.

## Aby sa to nemohlo zopakovať

Kokpit a zakladací skript žijú v dvoch rôznych repozitároch a **nič ich dovtedy neporovnávalo** — žiadna kontrola nikdy ten skript naozaj nespustila. Chyba tak prežila v tichosti a prejavila sa až pri prvom pokuse založiť projekt.

Pribudla kontrola, ktorá pri každej zmene spustí **skutočný zakladací skript so skutočnými argumentmi** a overí, že ich prijme. Overil som aj to, že tá kontrola naozaj dokáže zlyhať — dočasným vrátením chyby.
