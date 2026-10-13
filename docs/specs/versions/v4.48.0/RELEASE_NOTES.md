# v4.48.0

**Horná lišta pomenuje každú stránku kokpitu.** Na stránkach Ako začať, Poradca a Vizuál ukazovala len „NEX Studio
Visual“, takže nebolo vidno, kde si. Teraz píše ich meno rovnako ako ľavý panel. Kontrola v kokpite berie zoznam
stránok priamo zo smerovača, takže nová stránka už bez mena neostane.

**Rýchla oprava dostane číslo podľa verzie, ktorú má zákazník.** Doteraz si brala číslo od najvyššej verzie
projektu, aj keď sa ešte stavala. Pri NEX Manager, kde sa stavia 1.3.0 a na PROD beží 1.2.2, by oprava chyby z PROD
dostala číslo 1.3.1 namiesto 1.2.3 — a verzia 1.3.0 by potom nemala voľné číslo pre vlastnú opravu. Teraz sa číslo
odvodí od poslednej hotovej verzie. Keď je číslo obsadené nedokončenou opravou, kokpit povie ktorou a nič nezaloží.

**Zmazanie projektu zastaví aj jeho náhľad Vizuálu.** Kokpit pri zmazaní projektu uprace UAT, Znalostnú bázu aj
priečinok projektu, ale náhľad Vizuálu nechal bežať — frontend zmazaného projektu bol ďalej dostupný na adrese
náhľadu. Teraz sa zastaví a odstráni spolu s ostatným.
