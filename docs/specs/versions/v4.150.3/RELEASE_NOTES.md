# v4.150.3

**Nastavenia inštalácie sa prihlasujú do databázy tej inštalácie aj správnym používateľom a do správnej databázy.**
Oprava 4.150.2 zladila adresu databázy; staršie inštalácie (NEX Inbox u MÁGERSTAVu) si však vo vlastnom predpise
zakladajú aj vlastného používateľa a vlastnú databázu. Skúška nasadenia sa preto prihlasovala menom a do databázy
podľa projektu a databáza ju odmietla. Teraz skúška aj skutočné nasadenie berú používateľa a meno databázy z inštalácie
— z toho, ako sa jej nastavenia prihlasujú dnes, alebo z jej predpisu.
