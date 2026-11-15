# v4.142.0

**Príprava sa dozvie, čo prinieslo povýšenie šablóny a nex-shared pri založení verzie.** Keď si pri zakladaní verzie
zvolil novšiu šablónu aplikácie alebo nex-shared, Staviteľ v Príprave doteraz nevedel, z ktorej verzie na ktorú sa
ide ani čo zmeny prinášajú — Špecifikácia potom mohla tvrdiť, že verzia je bez povýšenia. Teraz zadanie Prípravy
povie, ktorý balík sa povýšil, z ktorej verzie na ktorú, a prinesie zápisy zmien medzi nimi; Špecifikácia musí uviesť,
čo z povýšenia verzia dotiahne a čo sa zmení pre ľudí (poznámky k verzii, návod na skúšku v UAT). Verzia bez povýšenia
dostane zadanie ako doteraz.
