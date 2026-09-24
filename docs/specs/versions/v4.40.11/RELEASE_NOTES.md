# v4.40.11

Nasadenie na server zákazníka je hotové — kokpit tam teraz zapíše aj popis inštalácie.

**Dovtedy ho písal len u seba.** Poistka, ktorá porovnáva popis kokpitu s tým na cieľovom serveri, preto našla rozdiel pri každom nasadení a zastavila ho — s radou „najprv prevezmi inštaláciu", hoci prevzatie práve prebehlo. Kokpit tak zacyklil sám seba a na server zákazníka sa nedalo nasadiť vôbec.

**Teraz popis aj nastavenia putujú tam, kde aplikácia beží.** Súbor na serveri zákazníka je totiž to, čo vidí každý, kto sa naň pozrie; keby starol, budúca ručná oprava by vychádzala z nesprávneho popisu — presne ten incident, ktorý sa nám stal.

**Heslá sa pri tom čítajú zo servera zákazníka, nie z kópie u nás.** Keby sa vzali od nás, aktualizácia by prepísala heslo k databáze zákazníka jeho starou verziou a on by sa k vlastným dátam nedostal. Obsah nastavení pritom putuje skrytým kanálom, nie v texte príkazu, ktorý na cudzom stroji vidia aj ostatní.

**A do ručne písanej inštalácie na cudzom serveri sa ďalej nezapisuje** — bez vedomého prevzatia. Tá istá ochrana, aká doteraz platila len pre inštalácie u nás.
