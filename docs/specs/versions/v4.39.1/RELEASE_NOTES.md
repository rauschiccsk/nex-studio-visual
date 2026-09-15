# v4.39.1 — prevzatie inštalácie nezahodí priečinky s faktúrami

Oprava jedného riadku v tom, čo si prevzatie inštalácie odnesie so sebou.

## Prečo

Verzia v4.39.0 zaviedla, že prevzatie ručne písanej inštalácie prenesie údaje, ktoré vie len ona
sama — pripojenia priečinkov, pridelené podsiete, poštových hostiteľov. Niesla však len **absolútne**
cesty, s odôvodnením, že priečinky vnútri inštalácie si generátor vytvorí sám.

Pri prvom ostrom použití sa ukázalo, že to odôvodnenie neplatí. Skúšobná inštalácia MÁGERSTAVU
ukladá originály faktúr do `originals` a vyexportované XML do `exports` — a zdrojový projekt o tých
priečinkoch nevie, lebo sám používa iné.

Prevzatie by ich zahodilo. Cesty v appke by zostali bez pripojenia na disk a ich obsah by sa stratil
pri každom ďalšom nasadení. Nie hlučne, ticho.

## Čo to znamená pre teba

**Prevzatie inštalácie si odnesie aj priečinky, ktoré patria jej samej** — nie len tie, ktoré ležia
inde na disku. Pri MÁGERSTAVE sú to priečinky s originálmi faktúr a s vyexportovaným XML.

Náhľad pred potvrdením ich odteraz vymenuje medzi tým, čo sa prenesie, takže je vidieť, o čo ide,
ešte pred kliknutím.

Pomenované úložiská Dockera sa nedotýkajú — tie si spravuje sám a nasadenie ich deklaruje zvlášť.
