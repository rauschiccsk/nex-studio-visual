# v4.40.29

„Pozastaviť“ už neklame. AI Agent vždy najprv dokončí úlohu, na ktorej práve robí, a až potom zastane —
to môže trvať aj hodinu. Kokpit však hneď po stlačení hlásil „Pozastavené“ a ponúkol „Vrátiť agentovi na
doplnenie“. Pokyn odoslaný v tej chvíli sa zapísal ako odoslaný, no agent ho nikdy nedostal, a pauza sa
pritom potichu zrušila.

Teraz kokpit, kým agent dorába úlohu, ukáže „Pozastavujem — AI Agent dokončí rozrobenú úlohu a potom
zastane“ a nič na stlačenie neponúkne. „Pozastavené“ uvidíš, až keď agent naozaj zastal; vtedy pokyn
naozaj odíde.

A všeobecne: kým agent pracuje, kokpit žiadny pokyn neprijme — povie „AI Agent ešte pracuje … Nič sa
neodoslalo.“ Nič sa už nezapíše ako odoslané, ak by sa to k agentovi nedostalo.
