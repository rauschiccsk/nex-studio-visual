# v4.83.1

**Aplikácia na novom serveri má kam ukladať dáta hneď od prvého spustenia.** Keď sa inštalácia zákazníka nasadzuje
na stroj, kde ešte nikdy nebežala, kokpit si pred jej spustením pripraví priečinky, do ktorých aplikácia ukladá dáta
(napríklad prijaté faktúry), a dá ich aplikácii. Doteraz ich pri prvom spustení vyrobil systém pre správcu servera
a aplikácia do nich nemohla zapisovať — prejavilo by sa to až pri prvom dokumente. Priečinky, ktoré už existujú,
ostávajú, ako sú.
