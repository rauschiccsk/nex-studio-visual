# v4.77.0

**Kokpit kontroluje pri Verifikácii každej verzie, či nepridala kód, ktorý nikto nepoužíva.** Funkcia, ktorú nič nevolá,
súbor, ktorý nič nenačíta, nepoužitý export či balík alebo trasa servera, ktorú nevolá žiadna obrazovka, verziu zastaví
— rovnako ako slovenský text v kóde. AI Agent dostane presný zoznam a vec odstráni. Zákazník tak neplatí za kód, ktorý
nič nerobí. Kód, ktorý volajú len skúšky, sa za použitý nepočíta.

**Zastaví sa len to, čo pribudlo vo verzii.** Starší nepoužívaný kód existujúcich aplikácií verziu nezablokuje — na
stránke projektu sa zobrazí ako „Nepoužívaný kód z minulých verzií“ a tlačidlo „Upratať nepoužívaný kód“ pripraví
rýchlu opravu so zoznamom; Manažér ju prečíta, vyberie druh práce a spustí sám.

**Výnimka len s dôvodom.** Čo vyzerá nepoužité, ale volá to niečo mimo kódu projektu (plánovač, iný systém), agent
zapíše s dôvodom do `docs/dead-code-exceptions.yml`. Výnimky pridané vo verzii Manažér uvidí vo výsledku Verifikácie
a Audítor ich preverí. Keď sa kontrola nedá spustiť, verziu nezastaví, ale výsledok povie, prečo neprebehla.
