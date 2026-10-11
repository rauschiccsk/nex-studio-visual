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
