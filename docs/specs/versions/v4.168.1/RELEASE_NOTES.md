# v4.168.1

**Nová premenná sa pri nasadení dostane ku každej službe, ktorá ju potrebuje.** Keď nová verzia aplikácie potrebovala
novú premennú, kokpit ju doplnil len backendu, frontendu a databáze. Ostatné služby, napríklad pracovník na pozadí, ju
nedostali. Orthodox Hermenia tak po nasadení verzie 1.1.0 nemala kód predplatného Claude práve v pracovníkovi, ktorý
volá AI. Teraz kokpit doplní premennú každej službe, ktorej ju projekt predpisuje. Hodnotu, ktorú si inštalácia drží
sama, pritom nikdy nenahradí hodnotou z projektu.
