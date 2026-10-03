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

Pokyn, ktorý pošleš agentovi, keď stavba stojí medzi úlohami, už nenahradí zadanie ďalšej úlohy plánu.
Doteraz agent dostal len tvoj pokyn — o úlohe, ktorá bola na rade, sa nedozvedel — a kokpit ju po jeho
odpovedi aj tak označil ako hotovú. Teraz dostane najprv tvoj pokyn a hneď za ním celé zadanie úlohy, takže
úloha sa uzavrie len vtedy, keď ju agent naozaj videl. Odpoveď na otázku, ktorú ti agent položil uprostred
úlohy, funguje ako doteraz.
