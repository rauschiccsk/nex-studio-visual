# v4.1.0 — kokpit prestal zamlčovať odpoveď

Pri ukladaní portu z cudzieho bloku prišlo odmietnutie správne — ale na
obrazovke stálo iba *„zadané údaje nie sú v poriadku"*, hoci aplikácia
odpovedala presne a menovala, komu blok patrí.

## Nebola to jedna zlá hláška

Chyba bola v jednom mieste, cez ktoré prechádza **každá chyba v celom
kokpite**: vetu od aplikácie vždy zahodilo a nahradilo konzervou podľa druhu
chyby, pravdu odložilo pod rozbaľovadlo *Technický detail*. Bola to teda
aplikácia, ktorá odpoveď pozná a zamlčí ju — všade.

## Prečo to tak vzniklo a čo sa zmenilo

Pôvodný zámer bol správny: surová anglická hláška manažérovi nepomôže. Ale
odpoveď na *„niektoré hlášky sú po anglicky"* je **preložiť ich**, nie skryť
všetky. Skryté sa totiž ani neopravia — kým sa nezobrazia, nikto nevidí, ktoré
ešte prekladu chýbajú.

Odteraz sa ukáže to, čo aplikácia naozaj odpovedala. Konzerva zostáva ako
záchranná sieť pre prípad, že veta nie je vôbec.
