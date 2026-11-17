# v4.154.0

**Nasadiť sa dá každá dokončená verzia — z kódu, s ktorým bola overená.** Doteraz kokpit pri nasadení staval
aktuálny kód projektu, takže verziu išlo nasadiť len dovtedy, kým sa v projekte nič nezmenilo — počas práce na novej
verzii sa posledná hotová nedala nasadiť vôbec. Teraz kokpit pri nasadení vezme presne ten kód, s ktorým bola verzia
overená, a z neho postaví aplikáciu aj jej inštaláciu. Výber verzie ponúka všetky dokončené verzie a zákazníkovi
predvolí tú, ktorá u neho beží alebo je novšia.

**Návrat na staršiu verziu je zastavený.** Staršia verzia nepozná databázu novšej a nenaštartovala by. Na PROD ju
kokpit nenasadí; na UAT len cez „Postaviť nanovo“, ktoré údaje UAT zmaže. Obrazovka to povie pri zákazníkovi ešte
pred kliknutím.
