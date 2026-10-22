# v4.69.0

**Slovenčinu v kóde zákazníka kokpit nájde hneď po úlohe a aj bez diakritiky.** Doteraz ju hľadal až pri
Verifikácii a len podľa písmen s diakritikou, takže mená skúšok ako „Ponuky“ alebo „test_override_aplikacie_422“
našiel až Audítor a verzia dostala ďalšie kolo opráv. Odteraz kokpit po každej dokončenej úlohe Programovania
skontroluje, čo úloha pridala, a slovenské komentáre či mená skúšok vráti AI Agentovi na preklad v tej istej úlohe.
Slovenčinu spozná aj bez diakritiky podľa slovníka slovenských slov. Text, ktorý aplikácia zobrazuje, ostáva po
slovensky a kvôli jazyku sa stavba nikdy nezastaví.
