# v4.40.18

Nasadenie na server zákazníka dostane na stavbu dosť času — a keď zlyhá, upratá po sebe.

**Viac času.** Aplikácia sa na serveri zákazníka stavia zo zdrojového kódu, ktorý sa tam musí najprv preniesť. Doteraz na to bolo pätnásť minút, rovnako ako pri inštalácii u nás. Jedna z aplikácií sa do toho nezmestila a nasadenie skončilo na časovom limite, hoci pracovalo správne. Pri nasadzovaní na cudzí server je limit odteraz podstatne dlhší; pri inštalácii u nás sa nič nemení.

**A keď nasadenie zlyhá, popis inštalácie sa vráti do pôvodného stavu.** Doteraz na serveri zákazníka zostal popis ukazujúci na verziu, ktorej obrazy sa nestihli postaviť. Kým aplikácia bežala, nikto si to nevšimol — ale pri najbližšom reštarte by sa nespustila vôbec, lebo by hľadala niečo, čo neexistuje. Po úspešnom nasadení sa, prirodzene, nevracia nič.

**Keby sa ani vrátenie nepodarilo, nasadenie to povie nahlas.** Taký stav je horší než samotné zlyhanie a človek sa o ňom musí dozvedieť hneď, nie pri najbližšom reštarte.
