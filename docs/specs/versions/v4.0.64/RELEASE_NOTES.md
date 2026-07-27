# v4.0.64 — NEX Studio Visual

## Ľavý panel — riadky presne 35 pixelov

Predchádzajúca úprava riadky stiahla, ale nie dosť — ponuka stále rolovala.

Príčinou bolo, že sa výška nastavovala **odsadením** a nie priamo. Emoji ikona sa vykresľuje vyššia, než by podľa nastavenia mala, a ťahala celý riadok so sebou: pri jednom odsadení vyšla výška 47 pixelov, pri menšom 40 — nikdy toľko, koľko malo. Riadok má teraz **pevne určenú výšku 35 pixelov** a ikona pevný rámček, takže ju už roztiahnuť nemôže.

Pätnásť položiek tak zaberie zhruba 555 pixelov namiesto pôvodných 705. Ponuka sa zmestí celá.
