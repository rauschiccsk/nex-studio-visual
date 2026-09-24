# v4.40.14

Prevzatie inštalácie ju naozaj vezme pod správu — dovtedy jej len prepísalo číslo verzie.

**Čo sa dialo.** Kokpit má pravidlo, že do inštalácie, ktorú už spravuje, nasadzuje verziu a nie prestavbu. To pravidlo je správne a zostáva: prestavba raz zhodila aplikáciu zákazníka na sedem minút. Použilo sa však aj na inštaláciu, ktorú kokpit **práve preberal** — a tam nedáva zmysel. Pri ostrej inštalácii, ktorá beží z vopred pripravených obrazov, tak vzniklo zadanie postaviť niečo, čo sa nemá odkiaľ vziať, a nasadenie sa zastavilo.

**Teraz prevzatie vykreslí inštaláciu zo zdrojového projektu.** Presne to prevzatie znamená a náhľad pred ním potvrdzuje, že sa pritom nič nestratí. Bežné nasadenie do už spravovanej inštalácie sa nemení — mení iba verziu, ako doteraz.

**A pri nasadzovaní na server zákazníka sa jeho stav najprv prenesie k nám.** Kokpit dovtedy počítal zmeny zo svojej vlastnej staršej kópie, hoci na serveri zákazníka ležal novší popis. Odteraz je jeho kópia verným odrazom toho, čo tam naozaj je.
