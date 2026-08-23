# v4.0.94 — technický tím má vlastný prístup, nie tvoje prihlásenie

Predchádzajúca verzia umožnila odpovedať zaseknutej stavbe, ale iba priamo na
serveri. Prakticky to znamenalo, že správy museli chodiť cez teba.

Teraz má náš technický tím **vlastný vstup do aplikácie** — nie účet, nie tvoje
prihlásenie, ale samostatné dvere s presne piatimi možnosťami: pozrieť, ktoré
stavby čakajú, pozrieť stav jednej, prečítať jej rozhovor, napísať správu
a odblokovať stavbu.

**Nič iné za tými dverami nie je.** Schváliť bránu alebo spustiť stavbu sa nedá
preto, že taká možnosť tam neexistuje — nie preto, že to niečo kontroluje.

## Bezpečnosť

Server **nedrží žiadne tajomstvo**, len jeho odtlačok. Aj keby sa niekto dostal
dovnútra, nenájde tam nič, čím by tie dvere otvoril.

Kontrola pred vydaním našla vážnu chybu: AI Agent si vedel prečítať prístupové
údaje a **podpísať sa ako technický tím** — odblokovať si vlastnú zaseknutú
stavbu tak, že to v zázname vyzeralo ako zásah človeka. Opravené predtým, než
sa to dostalo k tebe.

Písať sa dá **len do stavby, ktorá o pomoc požiadala**. Správa od technického
tímu nie je poznámka v rozhovore — agent sa ňou riadi, takže do zdravej stavby
nepatrí.
