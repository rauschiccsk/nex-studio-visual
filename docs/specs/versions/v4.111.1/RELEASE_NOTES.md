# v4.111.1

**Kontrola miesta pred nasadením meria disk, kam Docker naozaj píše.** Na serveri MAGER sú dáta Dockera od 04.11.2026
na veľkom dátovom disku; kokpit miesto pre rozbalené obrazy meral na pevnej starej ceste, teda na systémovom disku.
Teraz si cestu vezme z nastavenia servera, takže kontrola meria to isté miesto, ktoré stavba zapĺňa.
