# v4.40.6

Tri veci, ktoré spolu robia nasadzovanie na server zákazníka bezpečným.

**Kontrola po nasadení sa pýta toho istého servera, na ktorý sa nasadzovalo.** Keď ostrá prevádzka beží na vlastnom stroji zákazníka, kokpit tam aplikáciu aj overí — nie u seba. Bez toho by hlásil úspech podľa stroja, kde sa nič nezmenilo.

**Priečinky pre dáta zákazníka si na jeho serveri pripraví sám**, skôr než aplikáciu spustí. Inak by sa rozbehla, ale nemala by kam ukladať prijaté dokumenty.

**A nasadenie sa zastaví, keď sa nastavenie, ktoré drží kokpit, rozišlo s tým, čo na tom serveri naozaj beží** — alebo keď sa stav na cieli nedá prečítať. Doteraz by ho ticho prepísalo a zákazník by sa k aplikácii nemusel dostať.
