# v4.45.3

**Inštalácia aplikácie dostane aj súbory, ktoré jej projekt pripája, a cesty na serveri sa už nepomýlia s cestami
v aplikácii.** Career Asistent na UAT padal hneď po nasadení: kokpit mu neprenesol prázdny súbor, s ktorým
aplikácia beží bez vlastného certifikátu, a do nastavení inštalácie prevzal cestu, ktorá platí len vo vnútri
aplikácie. Teraz kokpit súbor z projektu do inštalácie prenesie a takú cestu do nastavení nepreberie. Inštalácii,
ktorú chyba už pokazila, to pri ďalšom nasadení opraví a v hlásení nasadenia povie, čo opravil. Priečinky s dátami
inštalácie, napríklad originály faktúr, sa nemenia.
