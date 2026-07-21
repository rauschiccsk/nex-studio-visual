# v4.0.22 — NEX Studio Visual

## Vizuál náhľad začína ukazovať REÁLNU aplikáciu (nie odpojenú kresbu)

Prvý krok opravy zásadného problému: to, čo Manažér schváli vo fáze **Vizuál**, sa doteraz mohlo od výslednej appky **úplne líšiť** — Vizuál ukazoval samostatný mockup (kresbu), ktorý nič neviazalo na to, čo sa reálne postaví.

Príčinou obchádzky (mockupu) bola technická prekážka: živý náhľad reálneho frontendu je za prihlásením (`ProtectedRoute`) a sandbox nemá backend → ukázala sa len **mŕtva prihlasovacia obrazovka**.

Od tejto verzie:

- **Živý Vizuál náhľad beží v „preview" móde** — sandbox spúšťa reálny frontend appky s premennou `VITE_PREVIEW`, cez ktorú sa aktivuje **preview harness**: mock prihlásenie (aby `ProtectedRoute` prešiel) + reprezentatívne dáta (MSW). Reálne obrazovky z nex-shared sa tak vykreslia **bez backendu a bez mŕtveho loginu**.
- **Pokyny pre Vizuál to vyžadujú** — AI pri stavaní obrazoviek nastaví ten preview harness, takže Manažér vidí a schvaľuje **reálnu appku**, nie kresbu.

Toto je prvý inkrement väzby „čo schváliš = to aj dostaneš". Ďalšie kroky (build verne preberie schválené obrazovky + nezávislé overenie zhody) prídu v ďalších verziách.
