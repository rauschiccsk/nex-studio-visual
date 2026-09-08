# v4.22.1 — zverenie projektu konečne funguje

Vo v4.22.0 sa tlačidlo „Zveriť projekt" tvárilo, že pracuje, a skončilo hláškou
**„Not Found"**. Moja chyba pri zapájaní.

## Čo bolo zle

Obe nové adresy som napísal s predponou `/projects`, hoci ju systém pridáva sám.
V appke tak vznikla cesta `/projects/projects/...` — kokpit volal správne miesto
a nikto tam nebol.

Nič sa tým nepokazilo a nič sa nestratilo; funkcia len nebola dostupná.

## Prečo to neodhalili skúšky

Päť stráží skúšalo, či presun robí správnu vec — a robil. Žiadna neskúšala, či je
**vôbec dostupný na adrese, ktorú kokpit volá**.

Nechytila to ani brána zhody kontraktu: tá porovnáva kokpit so serverom, a keď má
server zlú adresu, obe strany si zhodne rozumejú zle.

Pribudla stráž, ktorá sa pýta priamo bežiacej aplikácie, či tie dve adresy existujú
tam, kde ich kokpit hľadá. Overená v oboch smeroch.
