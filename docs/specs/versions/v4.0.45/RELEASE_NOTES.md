# v4.0.45 — NEX Studio Visual

## Živý náhľad (Vizuál) už neukáže prihlasovaciu stenu

Pri prvom otvorení fázy **Vizuál** sa občas namiesto obrazoviek aplikácie zobrazila **prihlasovacia stena** a nedalo sa cez ňu prejsť. Príčina bola v tom, ako sa štartoval náhľad: mockovací server (ktorý v náhľade nahrádza backend reprezentatívnymi dátami) sa načítaval až za behu — počas toho jedna požiadavka „prepadla" na neexistujúci backend a aplikáciu to tvrdo prehodilo na prihlásenie.

Opravené:

- **Náhľad má mockovací server pripravený hneď pri štarte**, takže od prvého načítania beží celý na mockovaných dátach a žiadna požiadavka neprepadne. Platí to **automaticky pre každú aplikáciu** — náhľad je odteraz spoľahlivý od prvého otvorenia.
- **Poistka pre budúce aplikácie:** AI Agent má v pokynoch zakotvené, že v náhľade sa nikdy nesmie presmerovať na prihlásenie (náhľad nemá backend ani prihlasovanie — manažér má vidieť obrazovky aplikácie).

Pripomenutie: aplikácie sú spúšťané cez token z NEX Managera — vlastné prihlasovanie menom a heslom do nich nepatrí. Náhľad teraz zobrazí priamo obrazovky aplikácie.
