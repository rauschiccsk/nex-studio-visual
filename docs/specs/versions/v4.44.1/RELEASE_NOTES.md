# v4.44.1

**Kontrola prepínača živého náhľadu číta aj nastavenie stavby aplikácie.** Pri Verifikácii kokpit kontroluje, či
aplikácia o živom náhľade rozhoduje len pomocníkom zo šablóny. Doteraz čítal len zdrojový kód frontendu. Nastavenie
stavby rozhoduje o tom, čo sa dostane do vydania, a NEX Manager v ňom mal tri miesta, kde by aj vypnutý náhľad
nechal vo vydaní jeho časti. Kontrola ich teraz nájde a AI Agent ich opraví v najbližšej verzii.

**Pri rýchlej oprave sa v poznámkach k vydaniu objaví veta pre zákazníka, nie zadanie pre agenta.** Agent pri
rýchlej oprave napíše jednou vetou, čo zákazník po oprave má. Kokpit ju od 7. septembra zahadzoval a do poznámok
k vydaniu dával celé zadanie pre agenta. To potom videl zákazník na záložke Aktualizácie a Manažér v UAT pod „Čo
otestovať“. Teraz tam je veta agenta. Poznámky starších rýchlych opráv, ktoré nikto neupravil, kokpit opraví
jednorazovo.
