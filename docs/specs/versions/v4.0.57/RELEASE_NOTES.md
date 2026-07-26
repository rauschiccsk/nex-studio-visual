# v4.0.57 — NEX Studio Visual

## Upozornenia vyzerajú všade rovnako — a už sa nemôžu rozísť

Oranžové upozornenie „niečo je inak, toto s tým sprav" existovalo v aplikácii dvakrát v dvoch kópiách: raz v Riadiacom centre („Overenie je zastarané") a raz na obrazovke nasadenia („Nasadenie je pozastavené"). Vyzerali rovnako, ale boli to dva samostatné kusy vzhľadu — pri najbližšej úprave farieb alebo rozostupov by sa začali od seba líšiť. Pritom ide o dve miesta toho istého postupu, medzi ktorými je jediné kliknutie.

Teraz obe kreslí jedna spoločná súčiastka. Na obrazovke sa nič nemení — mení sa to, že sa zmena vzhľadu premietne na obidve miesta naraz a nedá sa omylom spraviť len na jednom.

Tretie oranžové upozornenie (pri predbežnej previerke návrhu) má inú stavbu — nemá tlačidlo ani miesto na chybu — takže ostáva samostatné a preberá len spoločné farby. Násilne ho vtláčať do rovnakej formy by kód skomplikovalo, nie zjednodušilo.

## Doplnené chýbajúce testy

Upozornenie v Riadiacom centre doteraz nemalo ani jeden automatický test. Pribudlo ich sedem — okrem iného kontrolujú, že sa nezobrazuje, keď nemá čo ponúknuť, že sľubuje presne to, čo sa po kliknutí naozaj stane, a že pri zlyhaní to čestne prizná namiesto tvárenia sa, že overenie beží.
