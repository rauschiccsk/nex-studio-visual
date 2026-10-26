# v4.95.4

**Skúška po spustení si vyrobí aj skúšobný účet.** Povinné hodnoty, ktoré pri inštalácii zadáva Manažér (napríklad
prihlasovacie meno a počiatočné heslo), dostanú v dočasnom skúšobnom prostredí skúšobnú hodnotu: heslá a iné tajné
hodnoty náhodnú tým istým pravidlom ako UAT, ostatné pevnú. Nepovinné hodnoty zákazníka ostávajú prázdne. Na UAT
a PROD sa nič nemení — tam ich zadáva Manažér ako doteraz. Doteraz aplikácia, ktorá pri štarte zakladá prvý účet
(Author Asistent 0.1.0), v skúške nenaštartovala.
