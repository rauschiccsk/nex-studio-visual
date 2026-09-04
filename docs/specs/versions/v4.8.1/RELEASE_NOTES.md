# v4.8.1 — Rýchla oprava konečne donesie agentovi zadanie

Včera si prvýkrát klikol na **„Spustiť rýchlu opravu"**. Verzia vznikla, dráha sa rozbehla —
a agent sa okamžite zastavil s otázkou: *„Chýba mi zadanie."*

Pritom zadanie si napísal a uložilo sa správne.

## Čo sa dialo

Tvoja smernica sa zapísala ako správa do rozhovoru — do toho vlákna, ktoré vidíš **ty
v kokpite**. Lenže agent je samostatný program. Do rozhovoru nevidí a nikdy nevidel;
dostane jedinú vec — text pokynu pre svoju fázu.

A ten pokyn mu pri rýchlej oprave hovoril doslova: *„smernica je vyššie v tomto vlákne."*
Ukazoval na miesto, kam agent nedovidí. Pri novej verzii navyše začína s čistou hlavou,
takže „vyššie" nebolo vôbec nič.

Agent sa pozrel, nenašiel nič a spýtal sa. **Zachoval sa správne** — chyba bola na našej
strane.

## Čo sa mení

Pokyn pre rýchlu opravu teraz zadanie **nesie so sebou**. Agent ho má priamo pred sebou,
nemusí ho nikde hľadať.

Číta sa z tej istej uloženej správy, z ktorej vzniká aj úloha v pláne — jeden zdroj, takže
sa tie dve veci nemôžu rozísť. A prežije to aj reštart, lebo sa to číta z databázy, nie
z pamäte bežiaceho procesu.

Ak by smernica niekedy chýbala úplne, agent dostane jasný pokyn **spýtať sa a nič nemeniť**
— namiesto toho, aby ho niečo posielalo za zadaním, ktoré tam nie je.

## Prečo to nepadlo skôr

Táto diera bola v rýchlej dráhe **od začiatku**. Doteraz ju zakrývalo to, že rýchla oprava
sa spúšťala tak, že si smernicu poslal ešte raz ručne — a tá cesta fungovala.

Nové tlačidlo sprístupnilo dráhu na jedno kliknutie, a tým dieru odhalilo. Presne na to
sú ostré skúšky dobré.
