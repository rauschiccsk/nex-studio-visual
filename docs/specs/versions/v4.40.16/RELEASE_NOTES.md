# v4.40.16

Prevzatie inštalácie jej ponechá vlastné mená — a obraz si nájde, kto ho stavia, aj keď sa volá inak.

**Čo bolo zlé.** Kokpit zisťoval „kto stavia tento obraz" podľa jeho mena. Ručne písaná inštalácia si však meno obrazu zvolila sama a nemusí sedieť s tým, čo projekt vyrobí — u jedného zákazníka sa líšilo o jedinú pomlčku. Kokpit preto nevedel, odkiaľ obraz vziať, a nasadenie zastavil.

**Teraz sa hľadá aj podľa úlohy** — databáza, migrácie, backend, obrazovky — takže na mene obrazu už nezáleží. Cudzí obraz (napríklad samotný Postgres) sa zo zdrojových kódov naďalej nikdy nestavia.

**A prevzatie ide zase bežnou cestou**, ktorá inštalácii ponechá jej vlastné mená služieb, sietí a úložísk. Predchádzajúce vydanie ju pri prevzatí vykresľovalo podľa projektu; u aplikácie, ktorá si svoje časti pomenovala inak, by to znamenalo, že naštartuje s prázdnou databázou, kým tá pôvodná zostane vedľa bez odkazu. Meno služby je adresa k dátam a meno úložiska je miesto, kde tie dáta ležia — ani jedno sa pri nasadzovaní nemení.
