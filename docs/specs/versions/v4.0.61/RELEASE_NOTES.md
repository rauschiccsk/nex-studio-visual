# v4.0.61 — NEX Studio Visual

## Testy prehliadačovej časti konečne niekto spúšťa

V projekte je 52 súborov s 368 testami prednej časti aplikácie. **Nespúšťal ich žiadny kontrolný krok** — prechádzali len na počítači toho, kto na nich práve pracoval. Test, ktorý nikto nespúšťa, nie je poistka.

Automatická kontrola ich teraz púšťa pri každej zmene. Zároveň pribudol bežný príkaz `npm test`, takže sa dajú spustiť aj ručne bez toho, aby človek vedel, ako sa nástroj volá.
