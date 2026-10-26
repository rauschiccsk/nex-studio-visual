# v4.96.1

**Nasadenie na UAT nastaví adresu databázy aj projektu, ktorý ju v compose len preposiela.** Keď compose projektu
berie adresu databázy z nastavení inštalácie (`DATABASE_URL: ${DATABASE_URL:-}`), kokpit ju pri UAT nechal prázdnu.
Úprava databázy pri štarte (`migrate`) potom skončila „DATABASE_URL is not set“ a aplikácia sa nespustila (Author
Asistent 0.1.0). Teraz adresa vždy vedie na databázu UAT a ovládač (`+pg8000`) sa vezme z `.env.example`. Keby
adresa predsa vyšla prázdna, nasadenie sa zastaví ešte pred štartom a povie, ktoré služby ju potrebujú.
