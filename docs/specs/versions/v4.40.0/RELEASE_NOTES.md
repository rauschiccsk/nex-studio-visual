# v4.40.0 — nasadenie mení verziu, nie stavbu inštalácie

Nasadenie do už existujúcej inštalácie dosiaľ prestavovalo celú jej stavbu podľa vývojového
projektu. Odteraz do nej dodá novú verziu a ničoho iného sa nedotkne.

## Prečo

Pri prvom skutočnom nasadení NEX Inboxu k MÁGERSTAVU sa ukázalo, čo to znamená. Nasadenie prepísalo
inštalácii mená služieb, sieť, **názov databázy** aj **označenie zákazníka** — hodnotami z vývojového
projektu. Appka potom hľadala databázu, ktorá na tom stroji neexistuje, a bola sedem minút mimo
prevádzky. Dáta zostali nedotknuté a inštalácia sa obnovila zo zálohovaných súborov.

Pokúšali sme sa to riešiť tak, že sme vymenúvali údaje, ktoré treba zachovať. Za dva dni to boli
štyri kolá a každé odhalilo ďalší zabudnutý údaj. Zoznam toho, čím sa živá inštalácia líši od
vývojového projektu, sa nedá uzavrieť.

## Čo to znamená pre teba

**Nasadenie verzie mení verziu.** Mená služieb, sieť, priečinky, databáza, označenie zákazníka
aj prihlasovacie údaje zostávajú také, aké sú.

Je to to isté pravidlo, podľa ktorého nasadzujeme vlastné vydania NEX Studia — teraz platí aj pre
nasadenie k zákazníkovi.

**Prvé** nasadenie do prázdneho priečinka sa naďalej postaví podľa projektu, ako doteraz.

**Keď projekt pribudne o službu**, ktorú inštalácia nemá, nasadenie to **povie** a službu nedoplní.
Doplniť ju je samostatné rozhodnutie — nie vedľajší účinok nasadenia verzie.
