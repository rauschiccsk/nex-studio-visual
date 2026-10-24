# v4.88.0

**Poradca a tlačidlo „Priložiť k odpovedi agentovi“ pracujú len s tiketmi DEV.** Všetko, čo sa týka vývoja, sa vedie
v tiketoch DEV, a v zadaní verzie sa iný tiket neuvádza. Keď agent alebo zadanie predsa menuje tiket inej evidencie
(napríklad MAGER-66), kokpit ho nečíta a povie, že jeho obsah treba prevziať do tiketu DEV — nepriloží potom ani
ostatné tikety z tej istej odpovede, aby agent nedostal len polovicu.
