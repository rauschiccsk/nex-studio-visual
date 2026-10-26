# v4.95.6

**Skúška spustenia už neprehliadne službu, ktorá dookola padá.** Služba, ktorá nie je hore ani čisto nedobehla, napríklad
ju Docker stále reštartuje alebo vôbec nenaštartovala, znamená, že skúška spustenia neprešla. Hlásenie ju pomenuje
a pridá jej vlastný záznam, rovnako ako pri kontajneri, ktorý skončil chybou. Keď aplikácia odpovie, kokpit sa na služby
pozrie ešte raz. Služba, ktorá sa medzi dvoma pohľadmi reštartovala, padá, aj keď ju druhý pohľad zastihne bežať.
Doteraz takáto služba z hlásenia potichu vypadla: skúška Author Asistenta 0.1.0 hlásila „aplikácia sa spustila ✓“,
hoci jeho brána do internetu (egress) padala hneď pri štarte na chybe v nastavení.

**Neprejdená skúška pred vydaním nesie aj záznam služby, ktorá medzitým padla.** Doteraz niesla len výstup skriptu
(„egress proxy not reachable“) a príčinu musel Audítor hľadať na vlastnej kópii aplikácie.
