# v4.139.2

**Stránka „Inštalácie“ už správne ukazuje stav aplikácií v súkromnej sieti — na UAT aj na PROD.** Verzia 4.139.1 sa
ich pýtala zvnútra nesprávnym spôsobom a hlásila ich ako nedostupné, aj tie na PROD, ktoré predtým merala dobre.
Teraz sa kokpit pýta každej inštalácie najprv na jej adrese; iba keď ho súkromná brána na ANDROSe odmietne, spýta sa
jej backendu priamo, či odpovedá na svojej adrese zdravia.
