# v4.40.20

Pri nasadzovaní je vidieť, čo sa práve robí — a ako dlho to už trvá.

**Čo bolo zlé.** Nasadenie na server zákazníka trvá minúty: prenáša sa zdrojový kód, stavajú sa obrazy, reštartujú kontajnery. Na obrazovke bolo celý ten čas jediné slovo. Nedalo sa rozoznať, či sa pracuje, alebo či systém zamrzol — a kto to nevie, klikne znovu. Pri prevzatí ostrej inštalácie zákazníka je to to posledné, čo chceme.

**Teraz tlačidlo hovorí, v ktorom kroku nasadenie je** — pripravuje predpis, prenáša kód a stavia obrazy, spúšťa kontajnery, migruje databázu, overuje, upratuje — **a koľko času už uplynulo**. Kým ešte nie je čo hlásiť, ostáva pôvodný text; krok sa nevymýšľa.

**A kým jedno nasadenie beží, druhé sa nedá spustiť.** Kokpit ho odmietne a povie prečo: obe by siahali na tie isté súbory a tie isté kontajnery.
