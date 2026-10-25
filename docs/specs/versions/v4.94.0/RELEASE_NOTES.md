# v4.94.0

**Automatické kontroly projektov už nemajú prístup k serveru ANDROS.** Každý vykonávač kontrol má vlastný Docker
a doteraz bežal s plnými právami: krok kontroly, ktorý si stiahne cudzí balík, by sa cez neho dostal až k diskom
servera, kde bežia inštalácie zákazníkov. Docker vykonávača teraz beží bez práv správcu — disky servera v ňom nie sú
a kontajner s plnými právami sa v ňom nespustí. Kontroly bežia ako doteraz. Plné práva dostane len samostatný
vykonávač projektu pre úlohu, ktorá si ho vo svojom pracovnom postupe výslovne vypýta vlastným štítkom; na karte
vykonávača sa taká úloha počíta ako úloha projektu.
