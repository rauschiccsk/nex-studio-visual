# v4.173.0

**Podklady z internetu si kokpit stiahne sám.** Keď nový projekt potrebuje súbory z internetu, napríklad schémy
k DPH, vzory tlačív alebo príručku banky, Projektant ich v Nápadoch vymenuje v zadaní s presnou adresou. Kokpit ich pri
„Založiť projekt“ stiahne do priečinka docs/reference/ projektu a Staviteľ ich tam číta bez internetu. Manažér už nič
ručne nesťahuje.

**Bezpečne a overene.** Kokpit sťahuje len zo šifrovaných verejných adries, nikdy z vnútornej siete servera, a
každé presmerovanie overí znova. Súbor uloží, len keď je naozaj tým, čo sľubuje jeho meno — prihlasovacia alebo
chybová stránka sa namiesto PDF neuloží. Ku každému súboru zapíše zdroj, čas, veľkosť a odtlačok (ZDROJE.md).

**Manažér vidí, čo prišlo.** Na stránke verzie je časť „Podklady z internetu“: čo sa stiahlo, čo nie a prečo,
s tlačidlom „Stiahnuť znova“.

**Projektant nenechá zoznam neúplný.** Podklad bez presnej adresy súboru mu kokpit vráti na doplnenie skôr, než
zadanie uvidí človek.
