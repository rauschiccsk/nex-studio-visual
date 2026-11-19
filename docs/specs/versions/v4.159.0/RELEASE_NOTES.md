# v4.159.0

**Uložené tajomstvá zákazníka ako hviezdičky, Administrátor ich zobrazí.** V karte zákazníka sú uložené kľúče, tokeny
aj inicializačné heslo zobrazené hviezdičkami — vždy rovnako, bez ohľadu na dĺžku. Administrátor má na konci riadku
oko: kliknutím si tajomstvo zobrazí a ďalším kliknutím ho skryje. Zobrazenie nie je zmena — uloží sa len to, čo
napíšeš. Kokpit si zapíše, kto ktoré tajomstvo ktorého zákazníka a kedy zobrazil. Ostatní používatelia vidia len
hviezdičky.

**Inicializačné heslo administrátora sa dostane aj do aplikácií na šablóne NSV štandard.** Nasadenie takej aplikácie
(napríklad Orthodox Hermenia) dávalo heslu administrátora náhodnú hodnotu namiesto hesla zadaného pri zákazníkovi
a hlásilo ho medzi tajomstvami, ktoré „nikto nedodal“. Teraz dostane heslo zákazníka.
