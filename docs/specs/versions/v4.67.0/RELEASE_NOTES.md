# v4.67.0

**Poradca vidí vzor nastavení aplikácie, no bez hodnôt.** Súbor `.env.example` mu kokpit doteraz skrýval ako
tajomstvo, takže Poradca nevedel overiť značky, podľa ktorých kokpit pri nasadení vie, čo zadáva Manažér
(`@customer`, `@customer-secret`), a kam patrí verejná adresa (`@public-url`). Teraz vidí mená premenných, komentáre
a značky. Hodnotu nevidí, ani keby ju niekto do vzoru omylom napísal. Ostatné súbory s tajomstvami ostávajú pred ním
skryté ako doteraz.
