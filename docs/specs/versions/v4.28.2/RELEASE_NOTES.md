# v4.28.2 — prevzatie prejde, a keď nie, povie prečo

Druhý pokus o prevzatie NEX Managera skončil hláškou **„Unprocessable Entity"** a prázdnym
technickým detailom. Boli za tým dve chyby — jedna vecná a jedna v tom, ako sa o nej
hovorí.

## Zlá bola adresa repozitára

Prevzatie vracalo adresu presne tak, ako ju vypísal git:
`https://github.com/rauschiccsk/nex-manager.git`. Zakladanie projektu však čaká tvar
`owner/repo` — tak to má napísané pri tom poli a tak to majú uložené všetky tri existujúce
projekty. Prekladá sa to teraz; adresa, ktorej systém nerozumie, zostane prázdna a opýta
sa naň, namiesto aby prešla ďalej v tvare, ktorý bude odmietnutý.

## Horšie bolo, že sa to nedalo prečítať

Server napísal presnú vetu — *„Invalid repository format … Expected 'owner/repo'"* — ale na
obrazovku sa dostal iba názov stavu.

Príčina: keď je odpoveď servera **zložená** (objekt s vetou vnútri), hláška si z nej brala
len vonkajší obal. Bola to zámerná poistka proti tomu, aby sa manažérovi ukázalo
`[object Object]` — lenže spolu s tým zahodila aj vety, ktoré presne hovoria, čo je zle.

Veta vnútri objektu je stále veta. Odteraz sa vytiahne a ukáže. Poistka proti nezmyslu
zostáva.

Platí to pre **celý kokpit**, nielen pre prevzatie — cez to isté miesto ide každá chybová
hláška v appke.
