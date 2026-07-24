# v4.0.32 — NEX Studio Visual

## Vlastné konto a heslo pre každého používateľa

Doteraz si bežný používateľ (nie správca) nevedel pozrieť svoje konto ani zmeniť heslo a počiatočné heslo od správcu sa nevynucovalo zmeniť. Táto verzia to rieši:

- **Moje konto** — vľavo dole pri používateľovi pribudlo **Moje konto**, kde každý vidí svoje údaje (meno, prihlasovacie meno, e-mail, rola) a **zmení si vlastné heslo** (zadá súčasné + nové). Po zmene ostáva prihlásený, nevyhodí ho to na prihlásenie.
- **Vynútená zmena počiatočného hesla** — keď správca vytvorí používateľa a dá mu počiatočné heslo, používateľ je pri prvom prihlásení **vyzvaný nastaviť si vlastné heslo** skôr, než sa dostane do aplikácie. To isté platí po tom, ako mu správca heslo resetuje.
- **Prehľadnejšie Nastavenia** — karta **Používatelia** (správa cudzích kont) sa po novom zobrazuje **len správcovi**. Bežný používateľ tam už nevidí prázdny panel — svoje konto spravuje cez „Moje konto".

Bezpečnosť: pri zmene vlastného hesla treba potvrdiť súčasné heslo; správca môže heslo resetovať bez neho.
