# v4.116.1

**Kontrola jazyka kódu číta aj stránky, obrázky a štýly.** Kód, ktorý dostane zákazník, je celý po anglicky; kokpit
pri overení verzie hľadal slovenčinu v komentároch Pythonu, skriptov a nastavení, ale komentáre v HTML, SVG a CSS
nečítal. Teraz ich číta tiež — text, ktorý stránka ukazuje ľuďom, ostáva po slovensky.

**Údaje na viacerých obrazovkách aj v aplikáciách, ktoré kokpit stavia.** Agenti aplikácií na šablóne „NSV štandard“
majú v pravidlách, že každé spoločné úložisko údajov sa po zmene na serveri samo obnoví, alebo jedným riadkom povie,
prečo nemusí; stráž v aplikácii už nehádá, ktoré úložisko číta zo servera.

**Prístupy povedia, kedy sa súbor naposledy zmenil.** Po zmene obsahu súboru v Prístupoch sa zapíše aj čas úpravy.
