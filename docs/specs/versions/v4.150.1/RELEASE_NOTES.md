# v4.150.1

**Ostré nasadenie na MAGER už nehlási chybu len preto, že verejné DNS si ešte pamätá staré meno.** Keď kokpit pri
nasadení založí aplikácii meno v Cloudflare, verejné DNS servery ešte najviac päť minút odpovedajú tým, čo si
zapamätali predtým. Kontrola po nasadení sa ich pýtala a nasadenie zapísala ako neúspešné, hoci záznam bol správny
a aplikácia o chvíľu fungovala. Teraz kontrola verí samotnej zóne v Cloudflare: meno vedie na MAGER, keď to hovorí
zóna, aj keď niektoré servery ešte chvíľu pamätajú starú odpoveď. V hlásení nasadenia pribudne, do koľkých minút uvidí
nové meno aj zariadenie, ktoré sa naň pýtalo pred chvíľou.
