# v4.8.3 — Aj na kartu rozhodnutia sa dá odpovedať bez písania

Keď sa stavba zastaví a spýta sa ťa, ako ďalej, kokpit ti dovtedy neponúkal nič iné
než tú kartu. Žiadne „vrátiť agentovi", žiadne „odpovedať" — len dve voľby a textové
pole.

A po niekoľkých neúspešných kolách opráv engine **zámerne odoberá** jednoklikovú
voľbu „nechaj to opraviť", pretože ďalšie automatické kolo by nepomohlo. Zostane
jediná cesta vpred: **napísať pokyn rukou.**

## Prečo to vadilo

To pole je označené ako *nepovinné*, ale prakticky povinné je. Keď ho necháš prázdne,
agent dostane ako celé zadanie **názov voľby** — doslova vetu „Usmerniť opravu pre AI
Agenta". To mu nepovie nič.

Takže presne v chvíli, keď stavba uviazla a text má byť najpresnejší, si ho musel
napísať sám — hoci ho technický tím vedel pripraviť.

## Čo sa mení

Pribudlo piate sloveso: **odpoveď na kartu rozhodnutia**.

Technický tím ti pripraví text a v kokpite sa objaví lišta rovnako ako pri ostatných
návrhoch. Klikneš — a text odíde ako **tvoja vlastná odpoveď** na otvorenú otázku.
AI Agent ju dostane ako cielenú opravu a Auditor ju znova overí.

Text môžeš pred odoslaním upraviť, ako pri každom inom návrhu.

## Čo sa nemení

**Rozhoduje Manažér.** Karta sa nevyrieši sama a technický tím ju vyriešiť nevie — návrh
leží na tvojom stole, kým naň neklikneš.

Pribudli dve poistky. Návrh sa dá pripraviť len vtedy, keď je otvorená **práve jedna**
otázka — pri viacerých by nebolo jasné, ku ktorej ten text patrí, a hádať sa nebude.
A ak sa medzitým stavba pohne na **inú** otázku, odoslanie sa odmietne a povie prečo;
odpovedať na novú otázku textom písaným k starej by bolo horšie než neodpovedať.
