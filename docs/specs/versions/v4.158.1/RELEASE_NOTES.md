# v4.158.1

**Staviteľ nehlási zmenu databázy ako chybu NEX Studia.** Keď v projekte zmení databázu (napríklad na verziu
s rozšírením pre vyhľadávanie podľa významu), vie, že ju dostane až v ďalšom kroku stavby: zmenu zapíše, v hlásení
povie, ktoré skúšky ju overia neskôr, a stavba pokračuje. Predtým to hlásil ako chybu kokpitu a stavba zbytočne stála.
