# v4.43.22

**Kokpit hľadá webové rozhranie aplikácie na každom porte, ktorý aplikácia uvádza, nie len na prvom.** Pri skúške
spustenia vo Verifikácii aj pri overení po nasadení na UAT a PROD sa kokpit doteraz pýtal len na prvom porte.
Aplikácia, ktorá na ňom v danom prostredí nepočúva, padla ako „Webové rozhranie sa nespustilo“, hoci bežala —
Career Asistent uvádza HTTPS (443), ktoré beží až s certifikátom, a HTTP (80), na ktorom odpovedá vždy. Teraz
stačí, aby rozhranie odpovedalo na jednom z nich. Keď neodpovie na žiadnom, technické podrobnosti vymenujú každý
port a čo na ňom kokpit dostal, takže je vidieť, kde aplikácia nepočúva.

**„Nechaj to opraviť“ po zlyhanom spustení vedie k oprave.** Keď sa aplikácia vo Verifikácii nespustí, AI Agent
už nedostane pokyn spustiť ju cez Docker a chybu zopakovať — pri programovaní Docker nemá, takže jeden klik viedol
do slepej uličky. Dostane konkrétny dôvod zlyhania a záznam z kontajnera, ktorý neodpovedal, aj keď beží (doteraz
prišiel záznam len z kontajnera, ktorý spadol), a spustenie po oprave overí kokpit sám. Heslá a kľúče sú v zázname
zakryté — aj tvary ako `DB_PASSWORD=…`, ktoré kokpit doteraz prepúšťal.

**Rozhodovacia karta menuje rozhodnutie, z ktorého otázka vyplýva — otázkou a tvojou voľbou.** Pri otázke, ktorá je
dôsledkom tvojho skoršieho rozhodnutia, karta napíše napríklad „Vyplýva z tvojho rozhodnutia „Aký rozsah kontrol?“
(tvoja voľba: Široký).“ — doteraz tam stál vnútorný kľúč rozhodnutia, ktorý nič nepovie. Riadok „Zmena tejto voľby
znovu otvára N uzavretých rozhodnutí“ počíta len rozhodnutia, ktoré už naozaj máš zodpovedané, v ktoromkoľvek kole —
doteraz započítal aj nezodpovedané karty toho istého kola. Kokpit to číta z celého záznamu verzie, takže platí aj
pre rozhodnutia spred mnohých kôl. Je to len údaj: poradie možností ani značku „Odporúčané“ nemení.

**Nad rozhodovacími kartami je vidieť, kam stavba ide a čo posledné kolo previerky získalo.** Riadok „Uzavretých 12 ·
otvorené 4 · blokujúcich 5 → 1 → 0 → 1 → 0“ počíta z celého záznamu verzie (doteraz len z posledných správ, takže dlhej
stavbe vypadli prvé kolá) a len z kôl fázy, v ktorej karty sú — prvé kolo Verifikácie po čistom Návrhu už nevyzerá
ako návrat chýb. Pod ním veta o tom, čo kolo zachytilo, napríklad „Posledné kolo previerky zachytilo 3 nálezy (z toho
2 blokujúce); jeden z nich by prešiel všetkými skúškami a prejavil sa až u zákazníka.“ Audítor pri každom náleze
po novom povie, či by ho zachytili automatické skúšky. Zlé správy riadok nezakrýva: rad 0 → 2 → 5 sa vypíše tak, ako je.
