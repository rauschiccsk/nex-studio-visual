# v3.0.180 — Vyleštenie pred odovzdaním (2. časť)

Ďalšie tri drobné, ale dôležité vylepšenia úprimnosti — nadväzujú na predchádzajúcu verziu.

## Čo je nové

- **Zlyhané nasadenie už neostane skryté.** V prehľade zákazníkov (UAT/PROD) sa pri zákazníkovi, ktorému posledný pokus o nasadenie zlyhal, ukáže červené „Posledný pokus zlyhal“. Doteraz sa zobrazila len posledná úspešná verzia, takže neúspešná aktualizácia vyzerala ako v poriadku.
- **Chýbajúce nastavenie ≠ chyba v aplikácii.** Keď sa aplikácia nespustí pre chýbajúcu hodnotu v nastavení nasadenia (napríklad heslo k databáze) alebo obsadený port, systém to teraz povie presne a dodá „nie je to chyba v kóde aplikácie“ — namiesto zavádzajúceho „niektoré kontroly zlyhali“, ktoré nabádalo hľadať chybu v aplikácii.
- **Generované aplikácie držia svoju skutočnú verziu.** Aj v okrajovom prípade, keď verzia nie je známa, aplikácia ukáže čistú začiatočnú verziu 0.1.0 namiesto interného počítadla zostáv.
