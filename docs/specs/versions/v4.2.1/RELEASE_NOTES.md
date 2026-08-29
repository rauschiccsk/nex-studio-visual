# v4.2.1 — štyri veci, ktoré vyšli najavo pri jednej stavbe

Všetky štyri sa našli za dva dni na jednom projekte — od chvíle, keď mal
122 hotových úloh, po chvíľu, keď ho previerka pred vydaním odmietla. Sú tu
spolu, lebo patria k sebe: v každej z nich appka povedala niečo, čo neplatilo.

## „Nechaj to opraviť" doručilo prázdne zadanie

Previerka našla, že sa aplikácia nespustí. Manažér klikol odporúčané tlačidlo —
a pomocník odpovedal, že **nález sa k nemu nedostal**. Mal pravdu. Zadanie si
NEX Studio pripravilo správne, aj s presným dôvodom, ale vzápätí ho iné miesto
prepísalo na holú vetu *„Manažér rozhodol: → Nechaj to opraviť"*.

Horšie: k tomu pribudol pokyn **prepísať schválenú Špecifikáciu**. Ten pomocník
to odmietol a napísal, že by si zmeny musel vymyslieť. Menej opatrný by
schválený dokument prepísal na základe názvu tlačidla.

Teraz sa k pomocníkovi dostane celý nález — a pri oprave aplikácie sa pokyn
prepisovať dokumenty neobjaví vôbec.

## Popis v zozname súborov zhodil celú stavbu

Pomocník napísal do zoznamu vytvorených súborov cestu **aj s popisom**. Taká
„cesta" mala 347 znakov, čo je nad limitom súborového systému — a kontrola
namiesto odpovede „také niečo neexistuje" spadla a strhla so sebou celý ťah.
To sa stalo **po tom**, čo bola práca hotová a uložená.

Manažérovi sa pritom ponúklo tlačidlo „Skús znova", o ktorom sa dalo dopredu
povedať, že nepomôže — rovnaký vstup, rovnaký pád.

Kontrola je odteraz odolná: nesprávny zápis skončí ako **neúspešné overenie
s vysvetlením**, ktoré si pomocník opraví sám.

## Oprava po previerke konečne hovorí po ľudsky

Keď previerka niečo nájde, NEX Studio založí do plánu opravu. Osem z deviatich
skupín úloh malo ľudské vysvetlenie; deviata — práve tá o oprave — nemala
žiadne. Jediné miesto v pláne bez vysvetlenia bolo to, **kde sa niečo pokazilo**.

Teraz sa vysvetlenie odvodí priamo z nálezu previerky. A ak sa opravuje
opakovane, je z názvu vidieť **ktoré kolo** to je — predtým sa všetky volali
rovnako a raz to už viedlo k tomu, že sa tá istá oprava spravila trikrát.

## Plán ukazuje prácu ako bežiacu, kým beží

Funkcia so štyrmi hotovými úlohami a jednou bežiacou sa tvárila ako **„Čaká"** a
skupina nad ňou ako „Naplánované". Pravidlá boli napísané správne, len sa
prepočítavali až po dokončení úlohy, nie pri jej začatí. Pri dlhšej úlohe to
vyzeralo, že sa stavba zasekla.
