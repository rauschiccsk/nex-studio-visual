# v4.43.14

**Projekt môže byť dostupný len zo súkromnej siete.** Pri zakladaní projektu aj neskôr v jeho nastaveniach je voľba
„Prístup len zo súkromnej siete (Tailscale)“. Projekt s touto voľbou dostane pri testovacom aj ostrom nasadení a pri
náhľade Vizuálu adresu v `int.isnex.eu`, ktorá je dostupná len zo zariadení pripojených do Tailscale — verejnú
adresu nedostane vôbec. Po každom nasadení kokpit sám overí, že inštalácia nemá verejné meno, že jej meno ukazuje do
Tailscale a že server odinakiaľ nikoho nepustí; keď niečo z toho nesedí, povie to pri nasadení. Voľba je určená
pre aplikácie s osobnými údajmi, napríklad Career Asistent; ostatné projekty ostávajú, ako boli.
