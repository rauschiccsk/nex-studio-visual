# v4.32.0 — verziu už možno premenovať

Director si po dokončení stavby chcel opraviť názov verzie a napísal: *„Nemám možnosť
(aspoň som nenašiel) ako premenovať verziu."* Nenašiel preto, že tam nebola.

## Nastavenia verzie

Číslo verzie, Názov a Cieľový dátum sa dali zadať iba pri zakladaní. Potom už nikde —
stránka verzie ich len vypisovala. Hodnota, ktorú sa človek pomýlil raz, sa niesla navždy
a opraviť sa dala jedine zásahom do databázy.

Je to tá istá diera, ktorú pre projekty zavrelo *Nastavenia projektu*. Pre verzie sa to
nikdy neurobilo. Teraz má stránka verzie rovnaký panel.

## Číslo verzie sa zamyká — ale povie prečo

Číslo nie je iba popiska: podľa neho sa volá priečinok s dokumentmi. Premenovať verziu,
ktorá už svoje dokumenty má, by kokpit odviedlo na prázdny priečinok a hotová práca by
ostala ležať pod starým číslom.

Číslo sa preto dá meniť len dovtedy, kým podľa neho nič nevzniklo. Potom je pole
**zamknuté a napíše dôvod** — nie ticho nefunkčné. Pole, ktoré sa dá písať a nič nerobí, je
horšie než zamknuté: človek si myslí, že to, čo napísal, platí.

Zámok drží engine, nie obrazovka. Zamknuté pole v prehliadači nie je pravidlo, len nábytok —
kto pošle zmenu mimo obrazovky, dostane tú istú odpoveď.

Keď sa stav zámku nepodarí zistiť, pole sa **zamkne**, nie otvorí. Otvorené pole nad neznámym
stavom sľubuje zmenu, ktorú engine aj tak odmietne.

Názov a Cieľový dátum sa dajú meniť vždy.
