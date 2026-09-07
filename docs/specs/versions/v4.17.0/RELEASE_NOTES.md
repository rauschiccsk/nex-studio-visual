# v4.17.0 — Po nasadení sa NEX Studio do appky samo skúsi dostať

Doteraz sa po nasadení overovalo, že sa **dá podpísať vstupenka**. To je rozdiel
medzi „mám kľúč" a „odomkol som".

## Prečo to nestačilo

NEX ProductCatalogs mal všetky nastavenia deklarované správne a päť dní sa doň
nedalo vojsť. Svojmu NEX Manageru sa predstavoval jednou hlavičkou tam, kde žiada
dve, a potom čítal pole, ktoré neposiela. Deklarácia bola v poriadku, zmluva nie —
a to nechytila ani zelená sada testov, ani preberacia skúška, lebo obe overujú
appku proti jej **vlastnej predstave** o susedovi.

## Čo sa mení

Po nasadení do UAT NEX Studio vyrazí skutočnú vstupenku, zaklope na dvere appky
a **prejde až po prihlásené sedenie**.

To dopovedanie je celý zmysel: appka, o ktorú išlo, vstupenku prijala a odpovedala
korektne — a padla až na ďalšej otázke. Skúška, ktorá by skončila na presmerovaní,
by ju vyhlásila za zdravú. Tak ako všetko ostatné, päť dní.

## Čo uvidíš

Keď sa dnu nedostane, nasadenie **prejde** — appka je nasadená a beží, pokazený je
vstup — ale dostaneš vetu, ktorá menuje, ktorý článok praskol, a odporučí appku
zatiaľ neodovzdávať používateľom.

Tvrdé zlyhanie to zámerne nie je: raz už tvrdá kontrola na tomto mieste urobila
zo zákazníckej produkcie slepú uličku, z ktorej sa manažér nevedel dostať von.

Keď sa to zistiť nedá — appka neodpovedá, nemá tú cestu, nie je spárovaná — je
ticho. Poplach, ktorý kričí z neznalosti, sa človek naučí prehliadať.
