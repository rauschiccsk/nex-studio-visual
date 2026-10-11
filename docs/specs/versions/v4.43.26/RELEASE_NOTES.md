# v4.43.26

**Kokpit povie, keď sa aplikácia nedá otvoriť z NEX Managera.** Po každom nasadení NEX Managera alebo aplikácie, ktorá
sa z neho otvára, kokpit porovná, či obe strany počítajú s rovnakým spúšťacím lístkom — kľúč, meno inštalácie a meno
modulu — a nesúlad napíše pri zákazníkovi, napríklad „NEX Manager podpisuje menom inštalácie „local“, modul prijíma len
„uat-mager““. Doteraz to nezistil: kontrola po nasadení si skúšobný lístok vyrábala z nastavení samotnej aplikácie,
takže prešla vždy, aj keď skutočné otvorenie z NEX Managera zlyhávalo. Teraz ho vyrába tak, ako ho vyrába NEX Manager.

**Vykreslenie inštalácie nanovo jej nevezme vlastné nastavenia.** Keď sa inštalácia vykresľuje nanovo (napríklad pri
obnove tajomstiev), hodnoty, ktoré už mala — ako meno inštalácie — ostanú a kokpit povie, ktoré si ponechala.
