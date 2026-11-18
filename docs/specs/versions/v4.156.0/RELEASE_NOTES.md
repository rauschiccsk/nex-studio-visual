# v4.156.0

**Stavba a spustenie aplikácie majú jeden limit — 45 minút.** Platí pre nasadenie na každom stroji aj pre spustenie
aplikácie vo Verifikácii. Doteraz mal ANDROS 15 minút, do ktorých sa prvá stavba veľkého obrazu (napríklad aplikácie
s modelom umelej inteligencie) nemusela zmestiť. Bežiaca verzia počas stavby ďalej beží.

**Nová inštalácia sa prihlási do svojej databázy každou službou.** Keď projekt zapísal heslo databázy priamo do
predpisu, napríklad do migrácie, kokpit ho v novej inštalácii nahradí nastavením inštalácie — migrácia sa už
neodmietne prihlásiť.

**V Nápadoch je tlačidlo „Súhlasím“.** Na odporúčanie Projektanta odpovieš jedným klikom; rozpísaný text v poli ostane.

**Hlásenie chyby kokpitu zastaví stavbu aj počas opravy po Verifikácii.** Keď Staviteľ zistí, že chyba je v kokpite,
stavba sa zastaví a hlásenie dostane správca kokpitu — namiesto ďalších kôl opráv. Po oprave kokpitu možno overenie
spustiť znova bez opravy; ponúka to aj karta „Auditor po N kolách stále FAIL“.
