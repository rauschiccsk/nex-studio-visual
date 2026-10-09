# v4.43.14

**Projekt môže byť dostupný len zo súkromnej siete.** Pri zakladaní projektu aj neskôr v jeho nastaveniach je voľba
„Prístup len zo súkromnej siete (Tailscale)“. Projekt s touto voľbou dostane pri testovacom aj ostrom nasadení a pri
náhľade Vizuálu adresu v `int.isnex.eu`, ktorá je dostupná len zo zariadení pripojených do Tailscale — verejnú
adresu nedostane vôbec. Po každom nasadení kokpit sám overí, že inštalácia nemá verejné meno, že jej meno ukazuje do
Tailscale a že server odinakiaľ nikoho nepustí; keď niečo z toho nesedí, povie to pri nasadení. Voľba je určená
pre aplikácie s osobnými údajmi, napríklad Career Asistent; ostatné projekty ostávajú, ako boli.

**Kokpit si nainštaluješ ako aplikáciu vo vlastnom okne.** Na Prehľade je tlačidlo „Nainštalovať aplikáciu“ —
kokpit potom otvoríš z ikony NEX Studio Visual na ploche, vo vlastnom okne bez lišty prehliadača. Funguje na
zabezpečenej adrese `https://studio.int.isnex.eu`. Keď okno otvoríš po nasadení novej verzie, načíta si ju samo;
keď sa kokpit nasadí počas tvojej práce, okno ti novú verziu len ponúkne lištou — rozpísanú prácu nezahodí. Kokpit
sa z pamäte prehliadača nikdy nepodáva, takže stará verzia sa nemá odkiaľ objaviť.

**Agent vo Vizuáli použije prepínač náhľadu, ktorý projekt dostal zo šablóny.** Nový projekt má zo šablóny hotový
prepínač živého náhľadu: jedna časť drží ukážkové dáta mimo ostrého zostavenia, druhá rozhoduje, či sa náhľad
zapne. Pokyn agentovi vo Vizuáli ho teraz menuje — agent ho použije a nepíše si vlastný; projektu, ktorý ho ešte
nemá, ho vytvorí v rovnakom tvare. Aj inde v aplikácii, napríklad pri presmerovaní na prihlásenie, sa o náhľade
rozhoduje len cez tento prepínač. Predtým mohla aplikácia zostavená s nastavením „náhľad vypnutý“ omylom prestať
posielať používateľa s vypršaným prihlásením na prihlasovaciu obrazovku.

**Verifikácia skontroluje, ako aplikácia rozhoduje o živom náhľade.** Keď kód aplikácie rozhoduje o náhľade
inak než prepínačom zo šablóny, Verifikácia neprejde: kokpit ukáže, na koľkých miestach to je, a AI Agent dostane
presné súbory, riadky a správny tvar na opravu. Platí to aj vtedy, keď verziu schvaľuješ ručne. Takto sa zachytí
chyba, pri ktorej by sa aplikácia zostavená s vypnutým náhľadom správala ako náhľad — napríklad by používateľa
s vypršaným prihlásením neposlala na prihlasovaciu obrazovku.

**AI Agentovi priložíš súbor priamo v kokpite.** Keď agent potrebuje súbor, ktorý máš len ty — vzorový e-mail,
faktúru, export z iného systému —, klikneš v Riadiacom centre pri odpovedi agentovi alebo v rozhovore na
„Priložiť súbor“. Súbor sa uloží do projektu, do priečinka `private/`, ktorý git nevidí, takže sa do repozitára
nikdy nedostane; do tvojej správy sa sám doplní riadok, kde ho agent nájde. Čo v priečinku leží, vidíš
v Riadiacom centre pod „Súbory pre agenta“ — s veľkosťou a tým, kto súbor nahral — a môžeš to zmazať. Poradca
vidí len mená týchto súborov, nie ich obsah. Najväčší súbor, ktorý sa dá priložiť, má 25 MB.
