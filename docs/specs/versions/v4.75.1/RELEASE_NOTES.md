# v4.75.1

**Dokončené upratovanie kokpitu — preč je kód, ktorý volali už len skúšky.** Na obrazovke sa nič nemení. Z kokpitu
zmizli zvyšky starého rozhrania (služba chýb a úpravy či mazanie epík, featov a úloh, ktoré už nič nevolalo) a
pomocné funkcie, ktoré existovali len pre skúšky. Tam, kde skúška strážila takú náhradnú funkciu namiesto skutočného
kódu, stráži teraz skutočný kód — napríklad štítok siete stavby, podľa ktorého sa po páde dá upratať, alebo podpis
„Hotovo“. Nová kontrola zabráni, aby sa kód, ktorý volajú len skúšky, vrátil; vedomé pomôcky pre skúšky sú výslovne
označené.
