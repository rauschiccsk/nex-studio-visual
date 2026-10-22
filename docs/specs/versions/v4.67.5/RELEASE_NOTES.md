# v4.67.5

**Šablóny, ktoré kokpit vkladá do nových projektov, sú celé po anglicky.** Komentáre a hlášky v kontrolách projektu
(CI webu, kontrola pred zápisom, pomocník CI, skúška vydania) mali miestami slovenské slová a Audítor ich potom našiel
v kóde pre zákazníka, čo stálo kolo opráv navyše. Kokpit odteraz každú takú šablónu kontroluje rovnakým pravidlom ako
kód verzie. Kontrola jazyka navyše prečíta celý komentár aj vtedy, keď je v ňom apostrof — dovtedy jej časť vynechala.
