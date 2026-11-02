# v4.108.1

**Návrat na predošlú verziu po zlyhanom ostrom nasadení číta pri aplikáciách na šablóne aj záznam migrácií
šablóny.** Aplikácia na šablóne má dva záznamy migrácií — svoj a šablóny. Kokpit 4.108.0 porovnával len prvý, takže
keby šablóna stihla zmeniť svoje tabuľky a migrácia aplikácie potom zlyhala, spustil by predošlú verziu nad zmenenou
databázou. Teraz porovnáva oba a pri zmene ktoréhokoľvek nič nespúšťa.
