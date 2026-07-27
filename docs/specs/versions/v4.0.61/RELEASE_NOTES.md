# v4.0.61 — NEX Studio Visual

## Databáza sa pri nasadení povýši sama

Pri predchádzajúcom vydaní vyšlo najavo, že nasadenie nemalo krok, ktorý pripraví databázu na nový kód. Postup znel „postav obrazy, prepíš verziu, spusti" — a keby si niekto na tú prípravu nespomenul, aplikácia by nabehla ako zdravá a chyba by sa ukázala až vtedy, keď niekto otvorí dotknutú obrazovku a dostane hlášku o chybe.

Odteraz sa príprava databázy vykoná **automaticky pri štarte** a je súčasťou samotného balíka aplikácie. Nedá sa na ňu zabudnúť a nedá sa obísť — kdekoľvek sa aplikácia spustí, databáza bude zodpovedať kódu, ktorý s ňou prišiel.

Ak by príprava zlyhala, **aplikácia sa vedome nespustí**. Je to zámer: kontajner, ktorý odmietne nabehnúť, je vidieť na prvý pohľad — kontajner, ktorý beží a na jednej obrazovke vracia chybu, nie.

## Testy prehliadačovej časti konečne niekto spúšťa

V projekte je 52 súborov s 368 testami prednej časti aplikácie. **Nespúšťal ich žiadny kontrolný krok** — prechádzali len na počítači toho, kto na nich práve pracoval. Test, ktorý nikto nespúšťa, nie je poistka.

Automatická kontrola ich teraz púšťa pri každej zmene. Zároveň pribudol bežný príkaz `npm test`, takže sa dajú spustiť aj ručne bez toho, aby človek vedel, ako sa nástroj volá.
