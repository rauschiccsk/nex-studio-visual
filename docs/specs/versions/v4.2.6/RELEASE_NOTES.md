# v4.2.6 — plán sa už nezašpiní a prázdna hodnota nezabije kontajner

## Žiadna ďalšia skupina úloh pre opravy

Predošlá verzia zlúčila opravy po previerke do jednej skupiny — ale hľadala ju
len pod novým názvom. Na projekte, ktorý už niesol dve staršie, vznikla preto
**tretia vedľa nich**.

Hľadanie teraz rozpozná skupinu pod ktorýmkoľvek z jej názvov a ďalšie kolo
pridá do nej. Plán prestáva pribúdať do šírky.

## Prázdna hodnota vo vzorovom nastavení už kontajner nezabije

Skúška pred vydaním prvýkrát ukázala **skutočný** dôvod, prečo sa aplikácia
nespúšťa — a bol to tretí dôvod v poradí, iný než obe predošlé domnienky:
aplikácia spadla pri čítaní nastavenia `cors_allow_origins`.

Príčina bola nenápadná. Vo vzorovom súbore nastavení stálo `CORS_ALLOW_ORIGINS=`
bez hodnoty a skúška to skopírovala doslova. Pre bežiacu aplikáciu je to však
**priepastný rozdiel**: keď premenná nie je nastavená, použije sa jej vlastná
predvolená hodnota; keď je nastavená na prázdno, aplikácia sa ju pokúsi
prečítať — a spadne.

Prázdna hodnota vo vzorovom súbore znamená „toto doplň", nie „nastav na
prázdno". Takéto riadky sa už do skúšky neprenášajú.

## Prečo sa to našlo až teraz

Do predošlej verzie hlásila skúška len to, **ktorý** kontajner spadol. Odvtedy
prikladá aj jeho výpis — a hneď prvý beh ukázal príčinu, ktorú by nikto
neuhádol. Nikto by nespájal nastavenie webových adries s pádom databázových
migrácií.
