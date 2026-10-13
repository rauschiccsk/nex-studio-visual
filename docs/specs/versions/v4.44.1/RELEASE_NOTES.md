# v4.44.1

**Kontrola prepínača živého náhľadu číta aj nastavenie stavby aplikácie.** Pri Verifikácii kokpit kontroluje, či
aplikácia o živom náhľade rozhoduje len pomocníkom zo šablóny. Doteraz čítal len zdrojový kód frontendu. Nastavenie
stavby rozhoduje o tom, čo sa dostane do vydania, a NEX Manager v ňom mal tri miesta, kde by aj vypnutý náhľad
nechal vo vydaní jeho časti. Kontrola ich teraz nájde a AI Agent ich opraví v najbližšej verzii.
