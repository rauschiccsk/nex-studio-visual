# v4.172.4

**Krok, ktorý beží len raz, kokpit na sieť stroja nepripája a po nasadení sa ho nepýta.** Verzia 4.172.3 pripojila
na sieť stroja aj migráciu databázy NEX HelpDesk, lebo mala adresu NEX Knowledge. Migrácia po nasadení skončí, a tak
kontrola mien služieb hlásila šesťkrát „Kokpit nevedel overiť, kam z migrate vedie meno …“, hoci nebolo čo overovať.
Teraz kokpit služby, ktoré bežia len raz, na sieť stroja nepripája a kontrola mien ani kontrola čítania NEX Knowledge
sa ich nepýta. Inštalácia, kde už migrácia na sieti je, ostane tak, ako je, a upozornenia zmiznú.
