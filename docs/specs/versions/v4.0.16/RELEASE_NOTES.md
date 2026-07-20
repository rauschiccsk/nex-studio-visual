# v4.0.16 — NEX Studio Visual

## Keď sa automatická oprava nedarí dokola, systém to už netočí donekonečna

Doteraz sa mohlo stať, že záverečné overenie našlo **stále tú istú chybu**, automatická oprava ju neodstránila, a manažér mohol klikať „Nechaj to opraviť" znova a znova bez konca — každý pokus pritom minul čas a tokeny.

Od tejto verzie:

- **Systém rozpozná, že sa oprava netočí k výsledku** — keď to isté zlyhanie príde niekoľkokrát po sebe, karta prestane odporúčať ďalší pokus.
- **Namiesto toho čestne odporučí build zastaviť a odovzdať vývojárovi** — s jasným upozornením, že automatický agent sa zasekol (zvyčajne na nesprávnej príčine) a ďalší pokus pravdepodobne nepomôže.
- Skúsiť ešte raz sa dá, ale už to nie je odporúčaná voľba.

Koniec nekonečných slučiek; neexpert dostane čestnú informáciu, že treba človeka.
